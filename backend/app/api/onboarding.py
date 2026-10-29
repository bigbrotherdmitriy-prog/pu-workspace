from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import onboarding as svc
from app.core.auth import require_project_role, require_user
from app.database import get_db

router = APIRouter(prefix="/projects", tags=["onboarding"])


@router.get("/{project_id}/onboarding")
def project_onboarding(project_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return svc.steps(db, project_id)
