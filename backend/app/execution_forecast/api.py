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
    contract_id: int | None = Query(default=None, ge=1),
    schedule_item_id: int | None = Query(default=None, ge=1),
    row_limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Return a draft forecast.  This endpoint cannot approve or execute it."""
    require_project_role(db, user, project_id, "viewer")
    try:
        inputs = load_forecast_input(
            db, project_id, as_of,
            contract_id=contract_id,
            schedule_item_id=schedule_item_id,
            row_limit=row_limit,
        )
    except LookupError as exc:
        reason = str(exc)
        if reason in {"project_not_found", "contract_not_found", "schedule_item_not_found"}:
            raise HTTPException(404, "Forecast scope not found") from exc
        if reason == "forecast_row_limit_exceeded":
            raise HTTPException(422, "Forecast row limit exceeded") from exc
        raise HTTPException(409, "Forecast scope is not eligible") from exc
    try:
        return build_forecast(inputs)
    except ValueError as exc:
        raise HTTPException(422, "Forecast input is not supported") from exc
