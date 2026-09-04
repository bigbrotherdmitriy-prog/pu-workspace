from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.models.audit_log import AuditLog

router = APIRouter(prefix="/response-drafts", tags=["response-drafts"])


class DraftUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(draft|approved|rejected)$")
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=20000)


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
    before_status = draft.status
    edited = payload.subject is not None or payload.body is not None
    if payload.subject is not None:
        draft.subject = payload.subject.strip()
    if payload.body is not None:
        draft.body = payload.body.strip()
    if payload.status is not None:
        draft.status = payload.status
    db.add(AuditLog(
        action="response_draft_reviewed" if payload.status is not None else "response_draft_edited",
        entity_type="response_draft", entity_id=draft.id,
        details=f"status={before_status}->{draft.status}; edited={edited}; message={draft.message_id or 'none'}",
    ))
    db.commit()
    db.refresh(draft)
    return {"id": draft.id, "subject": draft.subject, "body": draft.body, "status": draft.status}
