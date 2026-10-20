from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.contract_stages import extract_contract_stages
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.contract_stage import ContractStage
from app.models.organization_contract import Contract
from app.models.user import User


router = APIRouter(prefix="/projects", tags=["contract-stages"])


def _get_contract(db: Session, project_id: int, contract_id: int) -> Contract:
    contract = db.get(Contract, contract_id)
    if contract is None or contract.project_id != project_id:
        raise HTTPException(status_code=404, detail="Договор не найден")
    return contract


def _serialize(stage: ContractStage) -> dict:
    amount = stage.amount if stage.amount is not None else Decimal("0.00")
    return {
        "id": stage.id,
        "object_name": stage.object_name,
        "stage_number": stage.stage_number,
        "title": stage.title,
        "planned_start": stage.planned_start.isoformat() if stage.planned_start else None,
        "planned_finish": stage.planned_finish.isoformat() if stage.planned_finish else None,
        "amount": f"{amount:.2f}",
        "vat_included": stage.vat_included,
        "evidence_quote": stage.evidence_quote,
        "status": stage.status,
    }


@router.get("/{project_id}/contracts/{contract_id}/stages")
def list_contract_stages(
    project_id: int,
    contract_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    contract = _get_contract(db, project_id, contract_id)
    stages = db.query(ContractStage).filter(
        ContractStage.contract_id == contract.id,
    ).order_by(ContractStage.object_name, ContractStage.stage_number).all()

    total = Decimal("0.00")
    by_object: dict[str, Decimal] = {}
    for stage in stages:
        amount = stage.amount or Decimal("0.00")
        total += amount
        key = stage.object_name or ""
        by_object[key] = by_object.get(key, Decimal("0.00")) + amount

    return {
        "items": [_serialize(s) for s in stages],
        "total": f"{total:.2f}",
        "by_object": {k: f"{v:.2f}" for k, v in by_object.items()},
    }


@router.post("/{project_id}/contracts/{contract_id}/stages/extract")
def extract_stages(
    project_id: int,
    contract_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    contract = _get_contract(db, project_id, contract_id)
    result = extract_contract_stages(db, project_id, contract)
    db.commit()
    return result


@router.post("/{project_id}/contracts/{contract_id}/stages/confirm")
def confirm_stages(
    project_id: int,
    contract_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    contract = _get_contract(db, project_id, contract_id)
    updated = db.query(ContractStage).filter(
        ContractStage.contract_id == contract.id,
        ContractStage.status == "proposed",
    ).all()
    for stage in updated:
        stage.status = "confirmed"
    db.add(AuditLog(
        action="contract_stages_confirmed",
        entity_type="contract",
        entity_id=contract.id,
        details=f"count={len(updated)}",
    ))
    db.commit()
    return {"confirmed": len(updated)}
