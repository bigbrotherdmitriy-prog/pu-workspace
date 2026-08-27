from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.ai_policy import ProjectAIPolicy
from app.models.audit_log import AuditLog
from app.models.user import User

router = APIRouter(prefix="/projects", tags=["ai-policy"])


class PolicyUpdate(BaseModel):
    mode: str = Field(pattern="^(local_only|external_allowed|redacted|metadata_only)$")
    dlp_enabled: bool = True


def _payload(item: ProjectAIPolicy | None, project_id: int) -> dict:
    return {"project_id": project_id, "mode": item.mode if item else "external_allowed",
            "dlp_enabled": item.dlp_enabled if item else True,
            "prompt_version": item.prompt_version if item else "v1"}


@router.get("/{project_id}/ai-policy")
def get_policy(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return _payload(db.get(ProjectAIPolicy, project_id), project_id)


@router.patch("/{project_id}/ai-policy")
def update_policy(project_id: int, payload: PolicyUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    item = db.get(ProjectAIPolicy, project_id)
    before = item.mode if item else "external_allowed"
    if item is None:
        item = ProjectAIPolicy(project_id=project_id, updated_by_user_id=user.id)
        db.add(item)
    item.mode = payload.mode
    item.dlp_enabled = payload.dlp_enabled
    item.updated_by_user_id = user.id
    db.add(AuditLog(action="ai_policy_updated", entity_type="project", entity_id=project_id,
                    details=f"mode={before}->{payload.mode}; dlp={payload.dlp_enabled}"))
    db.commit(); db.refresh(item)
    return _payload(item, project_id)
