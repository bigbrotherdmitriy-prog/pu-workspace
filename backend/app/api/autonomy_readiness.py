"""Read-only owner/manager AUTO readiness projection."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.autonomy_readiness import project_autonomy_readiness
from app.core.auth import require_user
from app.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


router = APIRouter(prefix="/api/v54/projects", tags=["v54-autonomy-readiness"])
_NO_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get("/{project_id}/autonomy-readiness")
def get_autonomy_readiness(
    project_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    project = db.scalar(select(Project).where(
        Project.id == project_id,
        Project.archived_at.is_(None),
    ))
    membership = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user.id,
    ))
    # Deliberately no User.is_admin bypass: this is a project owner/manager view.
    if project is None or membership is None or membership.role not in {"owner", "manager"}:
        raise HTTPException(404, "resource_unavailable")
    result = project_autonomy_readiness(db, project)
    if result is None:
        raise HTTPException(404, "resource_unavailable")
    response.headers.update(_NO_CACHE)
    return result
