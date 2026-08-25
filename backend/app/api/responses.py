from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.response_draft import ResponseDraft
from app.models.user import User

router = APIRouter(prefix="/response-drafts", tags=["response-drafts"])


@router.get("")
def list_drafts(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.execute(select(ResponseDraft, User).join(User, User.id == ResponseDraft.reviewer_user_id).where(ResponseDraft.project_id == project_id).order_by(ResponseDraft.created_at.desc(), ResponseDraft.id.desc())).all()
    return {"drafts": [{"id": d.id, "subject": d.subject, "body": d.body, "status": d.status, "source_file_name": d.source_file_name, "source_excerpt": d.source_excerpt, "confidence": d.confidence, "reviewer_name": u.name} for d, u in rows], "count": len(rows)}
