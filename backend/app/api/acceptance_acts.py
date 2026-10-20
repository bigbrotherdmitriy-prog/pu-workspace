from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.acceptance_act_document import (
    render_act_docx,
    stage_quantity,
    vat_from_gross,
)
from app.acceptance_acts import (
    build_act_data,
    create_stage_act,
    mark_act_signed,
    next_act_number,
    payment_days,
)
from app.contract_stages import contract_text
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.contract_stage import ContractStage
from app.models.execution_finance import AcceptanceAct, CashFlowEntry
from app.models.organization_contract import Contract
from app.models.user import User


router = APIRouter(prefix="/projects", tags=["acceptance-acts"])


def _get_contract(db: Session, project_id: int, contract_id: int) -> Contract:
    contract = db.get(Contract, contract_id)
    if contract is None or contract.project_id != project_id:
        raise HTTPException(status_code=404, detail="Договор не найден")
    return contract


def _get_stage(db: Session, contract: Contract, stage_id: int) -> ContractStage:
    stage = db.get(ContractStage, stage_id)
    if stage is None or stage.contract_id != contract.id:
        raise HTTPException(status_code=404, detail="Этап не найден")
    return stage


def _get_act(db: Session, project_id: int, act_id: int) -> AcceptanceAct:
    act = db.get(AcceptanceAct, act_id)
    if act is None or act.project_id != project_id:
        raise HTTPException(status_code=404, detail="Акт не найден")
    return act


def _payment_days_for_contract(db: Session, project_id: int, contract: Contract) -> tuple[int, bool]:
    texts = contract_text(db, project_id, contract)
    joined = "\n".join(text for _, _, text in texts)
    return payment_days(joined)


def _add_days(start: date, days: int, working: bool) -> date:
    if not working:
        return start + timedelta(days=max(days, 0))
    current = start
    remaining = max(days, 0)
    while remaining > 0:
        current = current + timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _cash_entries_by_act(db: Session, act_ids: list[int]) -> dict[int, CashFlowEntry]:
    if not act_ids:
        return {}
    entries = db.query(CashFlowEntry).filter(
        CashFlowEntry.source_name.in_([f"acceptance_act:{aid}" for aid in act_ids])
    ).all()
    by_act: dict[int, CashFlowEntry] = {}
    for entry in entries:
        if not entry.source_name:
            continue
        try:
            aid = int(entry.source_name.split(":", 1)[1])
        except (IndexError, ValueError):
            continue
        by_act[aid] = entry
    return by_act


@router.get("/{project_id}/contracts/{contract_id}/stage-register")
def stage_register(
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

    days, working = _payment_days_for_contract(db, project_id, contract)
    today = date.today()

    acts = db.query(AcceptanceAct).filter(
        AcceptanceAct.contract_id == contract.id,
        AcceptanceAct.status != "cancelled",
    ).all()
    act_by_stage: dict[int, AcceptanceAct] = {}
    for act in acts:
        if act.stage_id is None:
            continue
        prev = act_by_stage.get(act.stage_id)
        if prev is None or (act.id or 0) > (prev.id or 0):
            act_by_stage[act.stage_id] = act

    entries = _cash_entries_by_act(db, [a.id for a in acts])

    vat_rate = None
    if getattr(contract, "vat_mode", None) == "rate":
        vat_rate = getattr(contract, "vat_rate", None)

    totals: dict[str, dict[str, Decimal]] = {}
    items: list[dict] = []

    for stage in stages:
        amount = stage.amount if stage.amount is not None else Decimal("0.00")
        act = act_by_stage.get(stage.id)
        entry = entries.get(act.id) if act is not None else None

        if act is not None and act.status in ("signed", "paid") and act.act_date is not None:
            planned_receipt = _add_days(act.act_date, days, working)
        elif stage.planned_finish is not None:
            planned_receipt = _add_days(stage.planned_finish, days, working)
        else:
            planned_receipt = None

        actual_date = entry.actual_date if entry is not None else None
        overdue = bool(
            planned_receipt is not None and planned_receipt < today and actual_date is None
        )

        vat_value = vat_from_gross(amount, vat_rate) if vat_rate is not None else Decimal("0.00")

        items.append({
            "id": stage.id,
            "object_name": stage.object_name,
            "stage_number": stage.stage_number,
            "title": stage.title,
            "planned_start": stage.planned_start.isoformat() if stage.planned_start else None,
            "planned_finish": stage.planned_finish.isoformat() if stage.planned_finish else None,
            "amount": f"{amount:.2f}",
            "coefficient": str(stage_quantity(amount, amount) if amount else Decimal("0")),
            "vat": f"{vat_value:.2f}",
            "act": (
                {
                    "id": act.id,
                    "number": act.number,
                    "status": act.status,
                    "act_date": act.act_date.isoformat() if act.act_date else None,
                }
                if act is not None
                else None
            ),
            "planned_receipt_date": planned_receipt.isoformat() if planned_receipt else None,
            "actual_receipt_date": actual_date.isoformat() if actual_date else None,
            "overdue": overdue,
        })

        key = stage.object_name or ""
        bucket = totals.setdefault(key, {
            "price": Decimal("0.00"),
            "acted": Decimal("0.00"),
            "received": Decimal("0.00"),
        })
        bucket["price"] += amount
        if act is not None and act.status in ("signed", "paid"):
            bucket["acted"] += amount
        if entry is not None and entry.actual_amount is not None:
            bucket["received"] += Decimal(entry.actual_amount)

    total_price = Decimal("0.00")
    total_acted = Decimal("0.00")
    total_received = Decimal("0.00")
    by_object: dict[str, dict] = {}
    for key, bucket in totals.items():
        price = bucket["price"]
        acted = bucket["acted"]
        received = bucket["received"]
        remaining = price - acted
        by_object[key] = {
            "price": f"{price:.2f}",
            "acted": f"{acted:.2f}",
            "received": f"{received:.2f}",
            "remaining": f"{remaining:.2f}",
        }
        total_price += price
        total_acted += acted
        total_received += received

    return {
        "items": items,
        "totals": {
            "price": f"{total_price:.2f}",
            "acted": f"{total_acted:.2f}",
            "received": f"{total_received:.2f}",
            "remaining": f"{(total_price - total_acted):.2f}",
        },
        "by_object": by_object,
        "next_act_number": next_act_number(db, contract.id),
    }


class CreateActPayload(BaseModel):
    number: str | None = None


@router.post("/{project_id}/contracts/{contract_id}/stages/{stage_id}/act")
def create_act_endpoint(
    project_id: int,
    contract_id: int,
    stage_id: int,
    payload: CreateActPayload | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    contract = _get_contract(db, project_id, contract_id)
    stage = _get_stage(db, contract, stage_id)
    act = create_stage_act(
        db,
        project_id,
        contract,
        stage,
        user,
        number=payload.number if payload is not None else None,
    )
    db.commit()
    db.refresh(act)
    return {"id": act.id, "number": act.number, "status": act.status}


@router.get("/{project_id}/acceptance-acts/{act_id}/docx")
def download_act_docx(
    project_id: int,
    act_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    act = _get_act(db, project_id, act_id)

    if act.contract_id is None or act.stage_id is None:
        raise HTTPException(status_code=400, detail="Акт не связан с этапом договора")
    contract = db.get(Contract, act.contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Договор не найден")
    stage = db.get(ContractStage, act.stage_id)
    if stage is None:
        raise HTTPException(status_code=404, detail="Этап не найден")

    data = build_act_data(db, project_id, contract, stage, act.act_date)
    data.number = act.number
    payload = render_act_docx(data)

    filename = f"Акт_№{act.number}.docx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


class SignedPayload(BaseModel):
    act_date: date


@router.post("/{project_id}/acceptance-acts/{act_id}/signed")
def sign_act_endpoint(
    project_id: int,
    act_id: int,
    payload: SignedPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    act = _get_act(db, project_id, act_id)
    mark_act_signed(db, act, payload.act_date, user)
    db.commit()
    db.refresh(act)
    return {
        "id": act.id,
        "number": act.number,
        "status": act.status,
        "act_date": act.act_date.isoformat() if act.act_date else None,
        "cash_flow_entry_id": act.cash_flow_entry_id,
    }
