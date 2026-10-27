"""Data for the work-center HUD panels that no single existing endpoint gives:
stages per object as «breakers», ИД readiness per object, invoices waiting for review.

Owner 27.10.2026: the work center becomes a control-room screen (glass panels over a photo).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.money_calendar import _stage_order, _to_decimal, paid_stage_ids_for
from app.models.contract_stage import ContractStage
from app.models.execution_finance import AcceptanceAct, InvoiceExtractionProposal


def _stage_state(stage: ContractStage, act: AcceptanceAct | None, paid: bool, today: date) -> str:
    """ok = act signed or already paid by the customer, draft = act not signed yet,
    late = nothing done and the stage date is over, ahead = still to come."""
    if paid or (act is not None and act.status in ("signed", "paid")):
        return "ok"
    if act is not None:
        return "draft"
    if stage.planned_finish is not None and stage.planned_finish < today:
        return "late"
    return "ahead"


def stages_by_object(db: Session, project_id: int, today: date) -> list[dict[str, Any]]:
    stages = db.query(ContractStage).filter(ContractStage.project_id == project_id).all()
    if not stages:
        return []
    paid = paid_stage_ids_for(db, project_id, stages)
    acts = db.query(AcceptanceAct).filter(
        AcceptanceAct.project_id == project_id, AcceptanceAct.status != "cancelled",
    ).all()
    act_by_stage: dict[int, AcceptanceAct] = {}
    for act in acts:
        if act.stage_id is None:
            continue
        previous = act_by_stage.get(act.stage_id)
        if previous is None or (act.id or 0) > (previous.id or 0):
            act_by_stage[act.stage_id] = act
    groups: dict[str, list[ContractStage]] = {}
    for stage in sorted(stages, key=lambda s: (s.object_name or "", _stage_order(s))):
        groups.setdefault(stage.object_name or "", []).append(stage)
    result = []
    for name, items in groups.items():
        rows = []
        price = Decimal("0")
        acted = Decimal("0")
        for stage in items:
            state = _stage_state(stage, act_by_stage.get(stage.id), stage.id in paid, today)
            amount = _to_decimal(stage.amount)
            price += amount
            if state == "ok":
                acted += amount
            rows.append({
                "number": stage.stage_number, "state": state, "amount": f"{amount:.2f}",
                "finish": stage.planned_finish.isoformat() if stage.planned_finish else None,
            })
        result.append({"name": name or "Без объекта", "price": f"{price:.2f}", "acted": f"{acted:.2f}", "stages": rows})
    return result


def hud_extra(db: Session, project_id: int, today: date) -> dict[str, Any]:
    from app.executive_docs import object_report
    from app.models.executive_docs import IdSource

    names: list[str] = []
    for source in db.query(IdSource).filter(IdSource.project_id == project_id).all():
        if source.object_name and source.object_name not in names:
            names.append(source.object_name)
    readiness = []
    for name in names:
        report = object_report(db, project_id, name)
        readiness.append({
            "object": name, "percent": report.get("percent", 0),
            "required_total": report.get("required_total", 0),
            # object_report lists the missing titles; the screen needs how many.
            "missing": len(report.get("missing") or []) if isinstance(report.get("missing"), list) else int(report.get("missing") or 0),
        })
    to_check = db.query(InvoiceExtractionProposal).filter(
        InvoiceExtractionProposal.project_id == project_id,
        InvoiceExtractionProposal.status == "proposed",
    ).count()
    return {
        "objects": stages_by_object(db, project_id, today),
        "id_readiness": readiness,
        "invoices_to_check": to_check,
    }
