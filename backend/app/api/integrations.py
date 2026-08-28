from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.integrations.catalog import GOOGLE_CAPABILITIES, project_integration_catalog
from app.models.user import User

router = APIRouter(prefix="/integrations", tags=["integrations"])

@router.get("/project")
def project_integrations(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    adapters = project_integration_catalog(project_id, db)
    return {"project_id": project_id, "adapters": [item.as_dict() for item in adapters]}
