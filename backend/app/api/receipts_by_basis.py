from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.user import User
from app.receipts_by_basis import receipts_by_basis as build_receipts_by_basis
from app.receipts_by_basis import record_receipt

router = APIRouter(prefix="/projects", tags=["receipts-by-basis"])


class RecordReceiptRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    actual_date: date

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_before(cls, value: Any) -> Any:
        return Decimal(str(value))


@router.get("/{project_id}/receipts-by-basis")
def get_receipts_by_basis(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict[str, Any]:
    require_project_role(db, user, project_id, "viewer")
    return build_receipts_by_basis(db, project_id, date.today())


@router.post("/{project_id}/cash-flow/{entry_id}/record-receipt")
def post_record_receipt(
    project_id: int,
    entry_id: int,
    payload: RecordReceiptRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict[str, Any]:
    require_project_role(db, user, project_id, "manager")
    result = record_receipt(
        db,
        project_id,
        entry_id,
        payload.amount,
        payload.actual_date,
        user.id,
    )
    db.commit()
    return result
