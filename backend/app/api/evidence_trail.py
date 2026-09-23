"""Read-only project Evidence/Action Trail API."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.database import get_db
from app.evidence_trail import EvidenceTrailPage, project_evidence_trail
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


router = APIRouter(prefix="/api/v54/projects", tags=["v54-evidence-trail"])
_NO_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get("/{project_id}/evidence-trail", response_model=EvidenceTrailPage)
def read_project_evidence_trail(
    project_id: int,
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    # This first iteration is intentionally narrower than ordinary project
    # viewing.  Even global admins need an explicit owner/manager membership,
    # and non-members get the same response as an unknown project.
    membership = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user.id,
        ProjectMember.role.in_(("owner", "manager")),
    ))
    if db.get(Project, project_id) is None or membership is None:
        raise HTTPException(404, "Project not found")
    try:
        result = project_evidence_trail(db, project_id=project_id, limit=limit, cursor=cursor)
    except ValueError as error:
        if str(error) == "invalid_cursor":
            raise HTTPException(422, "Invalid pagination cursor") from None
        raise
    response.headers.update(_NO_CACHE)
    return result
