from email.utils import parseaddr
from hmac import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.models.audit_log import AuditLog
from app.models.ai_secretary import Message
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.provider_actions.product import _canonical_hash
from app.provider_actions.email_compensation import (
    DIRECT_UNDO_MESSAGE,
    EmailCompensationError,
    describe_email_compensation,
    propose_email_compensation,
)

router = APIRouter(prefix="/response-drafts", tags=["response-drafts"])


class DraftUpdate(BaseModel):
    expected_review_token: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    status: str | None = Field(default=None, pattern="^(draft|approved|rejected)$")
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    recipient_to: str | None = Field(default=None, min_length=3, max_length=1000)


class EmailCompensationProposal(BaseModel):
    expected_source_etag: str = Field(pattern="^[0-9a-f]{64}$")


def _review_token(db: Session, draft: ResponseDraft) -> str:
    """Exact review fingerprint, not an authorization or provider approval."""
    project = db.get(Project, draft.project_id, populate_existing=True)
    source = db.get(Message, draft.message_id, populate_existing=True) if draft.message_id else None
    if project is None or project.archived_at is not None or (draft.message_id and (
            source is None or source.project_id != draft.project_id
            or source.organization_id != project.organization_id)):
        raise HTTPException(409, "draft_review_context_unavailable")
    return _canonical_hash({
        "kind": "response-draft-review", "version": 1,
        "draft": {name: getattr(draft, name) for name in (
            "id", "project_id", "reviewer_user_id", "organizer_session_id", "message_id",
            "subject", "body", "recipient_to", "status", "source_file_id", "source_file_name",
            "source_excerpt", "source_excerpt_hash", "confidence", "sent_external_id")},
        "project": {"id": project.id, "organization_id": project.organization_id,
                    "record_version": project.record_version},
        "source": None if source is None else {name: getattr(source, name) for name in (
            "id", "organization_id", "project_id", "contract_id", "mail_connection_id",
            "provider_message_id", "source_reference_id", "context_version", "origin_version",
            "context_confirmed", "source_type", "source_external_id", "source_sender",
            "source_thread_id", "content", "context_evidence")},
    })


def _locked_review(db: Session, draft_id: int, user: User, minimum: str) -> ResponseDraft:
    # This HTTP command owns its transaction. Never flush/overwrite pending
    # content or authority edits, including through an expired identity-map row.
    guarded = (ResponseDraft, Project, ProjectMember, Message, User)
    if any(isinstance(row, guarded) for row in (*db.new, *db.dirty, *db.deleted)):
        raise HTTPException(409, "draft_review_pending_changes")
    with db.no_autoflush:
        scope = db.execute(select(ResponseDraft.project_id, ResponseDraft.message_id)
                           .where(ResponseDraft.id == draft_id)).one_or_none()
        if scope is None:
            raise HTTPException(404, "Response draft not found")
        require_project_role(db, user, scope.project_id, minimum)
        project = db.scalar(select(Project).where(Project.id == scope.project_id)
                            .with_for_update().execution_options(populate_existing=True))
        if project is None or project.archived_at is not None:
            raise HTTPException(409, "draft_review_context_unavailable")
        # Project -> source Message -> draft: the context analysis owns Message
        # before producing drafts. Do not invert that order during human review.
        if scope.message_id is not None:
            db.scalar(select(Message).where(Message.id == scope.message_id)
                      .with_for_update().execution_options(populate_existing=True))
        draft = db.scalar(select(ResponseDraft).where(ResponseDraft.id == draft_id)
                          .with_for_update().execution_options(populate_existing=True))
        if draft is None or (draft.project_id, draft.message_id) != tuple(scope):
            raise HTTPException(409, "draft_review_changed")
        require_project_role(db, user, draft.project_id, minimum)
        return draft


@router.get("")
def list_drafts(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if any(isinstance(row, (ResponseDraft, Project, ProjectMember, Message, User))
           for row in (*db.new, *db.dirty, *db.deleted)):
        raise HTTPException(409, "draft_review_pending_changes")
    with db.no_autoflush:
        require_project_role(db, user, project_id, "viewer")
        rows = db.execute(select(ResponseDraft, User).join(User, User.id == ResponseDraft.reviewer_user_id).where(ResponseDraft.project_id == project_id).order_by(ResponseDraft.created_at.desc(), ResponseDraft.id.desc()).execution_options(populate_existing=True)).all()
        return {"drafts": [{"id": d.id, "subject": d.subject, "body": d.body, "status": d.status, "source_file_name": d.source_file_name, "source_excerpt": d.source_excerpt, "confidence": d.confidence, "reviewer_name": u.name, "recipient_to": d.recipient_to, "review_token": _review_token(db, d)} for d, u in rows], "count": len(rows)}


@router.patch("/{draft_id}")
def update_draft(draft_id: int, payload: DraftUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = _locked_review(db, draft_id, user, "manager" if payload.status == "approved" else "editor")
    if draft.status == "sent" or draft.sent_external_id:
        raise HTTPException(409, "Отправленное письмо неизменяемо; подготовьте корректирующий ответ")
    if draft.status not in {"draft", "approved", "rejected"}:
        raise HTTPException(409, "draft_review_not_editable")
    if draft.source_file_name == "corrective-follow-up" and payload.status is not None:
        raise HTTPException(409, "Корректирующий ответ остаётся черновиком до отдельного CONFIRM approval")
    if payload.expected_review_token is None:
        raise HTTPException(409, "draft_review_required")
    if not compare_digest(payload.expected_review_token, _review_token(db, draft)):
        raise HTTPException(409, "draft_review_changed")
    before_status = draft.status
    changes = {}
    for name in ("subject", "body"):
        value = getattr(payload, name)
        if value is not None:
            if not value.strip():
                raise HTTPException(422, "draft_content_invalid")
            changes[name] = value.strip()
    if payload.recipient_to is not None:
        candidate = payload.recipient_to.strip().casefold()
        parsed = parseaddr(candidate)[1].casefold()
        if (not parsed or parsed != candidate or candidate.count("@") != 1
                or candidate.startswith("@") or candidate.endswith("@")
                or any(separator in candidate for separator in (",", ";", "\r", "\n"))):
            raise HTTPException(422, "Введите один корректный email получателя")
        changes["recipient_to"] = candidate
    edited = any(getattr(draft, name) != value for name, value in changes.items())
    if payload.status == "approved" and edited:
        raise HTTPException(409, "draft_save_before_approval")
    for name, value in changes.items():
        setattr(draft, name, value)
    if payload.status is not None:
        draft.status = payload.status
    elif edited and draft.status == "approved":
        # Approval binds the exact human-visible envelope. Any later mutation,
        # including the recipient, must require a fresh explicit confirmation.
        draft.status = "draft"
    db.add(AuditLog(
        action="response_draft_reviewed" if payload.status is not None else "response_draft_edited",
        entity_type="response_draft", entity_id=draft.id,
        details=f"status={before_status}->{draft.status}; edited={edited}; message={draft.message_id or 'none'}",
    ))
    db.commit()
    db.refresh(draft)
    return {"id": draft.id, "subject": draft.subject, "body": draft.body,
            "recipient_to": draft.recipient_to, "status": draft.status,
            "review_token": _review_token(db, draft)}


@router.get("/{draft_id}/email-compensation")
def read_email_compensation(draft_id: int, db: Session = Depends(get_db),
                            user: User = Depends(require_user)):
    draft = db.get(ResponseDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "Response draft not found")
    require_project_role(db, user, draft.project_id, "manager")
    return describe_email_compensation(db, draft)


@router.post("/{draft_id}/email-compensation/proposals")
def create_email_compensation_proposal(
    draft_id: int,
    payload: EmailCompensationProposal,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    draft = db.get(ResponseDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "Response draft not found")
    require_project_role(db, user, draft.project_id, "manager")
    try:
        result = propose_email_compensation(
            db, draft,
            expected_source_etag=payload.expected_source_etag,
            actor_id=str(user.id),
            correlation_id=request.headers.get("X-Request-ID", "request-unavailable"),
        )
        db.commit()
        return result
    except EmailCompensationError as exc:
        db.rollback()
        raise HTTPException(
            409,
            f"{DIRECT_UNDO_MESSAGE}. Корректирующий ответ сейчас недоступен ({exc.code})",
        ) from exc
