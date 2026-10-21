from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.money_calendar import money_calendar as build_money_calendar

router = APIRouter(prefix="/projects", tags=["money-calendar"])


@router.get("/{project_id}/money-calendar")
def get_money_calendar(
    project_id: int,
    horizon_days: int = Query(45, ge=1, le=365),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    return build_money_calendar(db, project_id, today=date.today(), horizon_days=horizon_days)
