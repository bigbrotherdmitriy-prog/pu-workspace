from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.execution_forecast.engine import build_forecast
from app.execution_forecast.repository import load_forecast_input
from app.models.user import User


router = APIRouter(prefix="/execution/forecast", tags=["execution-forecast"])


@router.get("/{project_id}")
def get_explainable_forecast(
    project_id: int,
    as_of: date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Return a draft forecast.  This endpoint cannot approve or execute it."""
    require_project_role(db, user, project_id, "viewer")
    try:
        inputs = load_forecast_input(db, project_id, as_of)
    except LookupError as exc:
        raise HTTPException(404, "Project not found") from exc
    return build_forecast(inputs)
