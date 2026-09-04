from email.utils import parseaddr

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.models.audit_log import AuditLog
from app.provider_actions.email_compensation import (
    DIRECT_UNDO_MESSAGE,
    EmailCompensationError,
    describe_email_compensation,
    propose_email_compensation,
)

router = APIRouter(prefix="/response-drafts", tags=["response-drafts"])


class DraftUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(draft|approved|rejected)$")
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    recipient_to: str | None = Field(default=None, min_length=3, max_length=1000)


class EmailCompensationProposal(BaseModel):
    expected_source_etag: str = Field(pattern="^[0-9a-f]{64}$")


@router.get("")
def list_drafts(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.execute(select(ResponseDraft, User).join(User, User.id == ResponseDraft.reviewer_user_id).where(ResponseDraft.project_id == project_id).order_by(ResponseDraft.created_at.desc(), ResponseDraft.id.desc())).all()
    return {"drafts": [{"id": d.id, "subject": d.subject, "body": d.body, "status": d.status, "source_file_name": d.source_file_name, "source_excerpt": d.source_excerpt, "confidence": d.confidence, "reviewer_name": u.name, "recipient_to": d.recipient_to} for d, u in rows], "count": len(rows)}


@router.patch("/{draft_id}")
def update_draft(draft_id: int, payload: DraftUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = db.get(ResponseDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "Response draft not found")
    require_project_role(db, user, draft.project_id, "editor")
    if payload.status == "approved":
        require_project_role(db, user, draft.project_id, "manager")
    if draft.status == "sent":
        raise HTTPException(409, "Отправленное письмо неизменяемо; подготовьте корректирующий ответ")
    if draft.source_file_name == "corrective-follow-up" and payload.status is not None:
        raise HTTPException(409, "Корректирующий ответ остаётся черновиком до отдельного CONFIRM approval")
    before_status = draft.status
    edited = payload.subject is not None or payload.body is not None or payload.recipient_to is not None
    if payload.subject is not None:
        draft.subject = payload.subject.strip()
    if payload.body is not None:
        draft.body = payload.body.strip()
    if payload.recipient_to is not None:
        candidate = payload.recipient_to.strip().casefold()
        parsed = parseaddr(candidate)[1].casefold()
        if (not parsed or parsed != candidate or candidate.count("@") != 1
                or candidate.startswith("@") or candidate.endswith("@")
                or any(separator in candidate for separator in (",", ";", "\r", "\n"))):
            raise HTTPException(422, "Введите один корректный email получателя")
        draft.recipient_to = candidate
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
            "recipient_to": draft.recipient_to, "status": draft.status}


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
