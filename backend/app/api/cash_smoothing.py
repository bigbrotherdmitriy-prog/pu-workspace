from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.cash_smoothing import propose_smoothing
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.execution_finance import CashFlowEntry

router = APIRouter(prefix="/projects", tags=["cash-smoothing"])


@router.get("/{project_id}/cash-flow/smoothing")
def cash_flow_smoothing(
    project_id: int,
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    require_project_role(db, user, project_id, "viewer")
    rows = db.query(CashFlowEntry).filter(CashFlowEntry.project_id == project_id).all()
    return propose_smoothing(rows, today=date.today())
