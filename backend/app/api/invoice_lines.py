from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app import invoice_lines as invoice_lines_service

router = APIRouter(prefix="/projects", tags=["invoice-lines"])


class MatchBody(BaseModel):
    purchase_item_id: int | None = None


@router.post("/{project_id}/cash-flow/{entry_id}/invoice-lines/extract")
def extract_invoice_lines(
    project_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    return invoice_lines_service.extract_lines(db, project_id, entry_id)


@router.get("/{project_id}/invoice-lines")
def get_invoice_lines(
    project_id: int,
    entry_id: int | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    return {
        "lines": invoice_lines_service.list_lines(db, project_id, entry_id=entry_id),
    }


@router.post("/{project_id}/invoice-lines/{line_id}/match")
def match_invoice_line(
    project_id: int,
    line_id: int,
    body: MatchBody,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    return invoice_lines_service.set_match(
        db, project_id, line_id, body.purchase_item_id,
    )
