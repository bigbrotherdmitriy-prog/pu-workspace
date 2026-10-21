from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.contract_stage import ContractStage
from app.models.execution_finance import AcceptanceAct, CashFlowEntry
from app.models.organization_contract import Contract


_EXCLUDED_STATUSES = ("cancelled", "deleted", "rejected")


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _level(days_left: Optional[int]) -> str:
    if days_left is None:
        return "none"
    if days_left < 0:
        return "overdue"
    if days_left <= 2:
        return "critical"
    if days_left <= 6:
        return "soon"
    if days_left <= 14:
        return "near"
    return "ok"


def _days_left(due: Optional[date], today: date) -> Optional[int]:
    if due is None:
        return None
    return (due - today).days


def money_calendar(
    db: Session,
    project_id: int,
    *,
    today: date,
    horizon_days: int = 45,
) -> Dict[str, Any]:
    horizon_date = today + timedelta(days=horizon_days)
    back_date = date(1900, 1, 1)  # every unpaid plan row counts; long-past ones go to «unmarked»
    items: List[Dict[str, Any]] = []

    # a) pay_invoice
    invoices = (
        db.query(CashFlowEntry)
        .filter(CashFlowEntry.project_id == project_id)
        .filter(CashFlowEntry.direction == "outflow")
        .filter(CashFlowEntry.status.notin_(_EXCLUDED_STATUSES))
        .all()
    )
    for entry in invoices:
        invoiced = _to_decimal(entry.invoiced_amount)
        if invoiced <= 0:
            continue
        actual = _to_decimal(entry.actual_amount)
        remaining = invoiced - actual
        if remaining <= 0:
            continue
        due = entry.planned_date
        days_left = _days_left(due, today)
        items.append({
            "kind": "pay_invoice",
            "title": entry.title,
            "counterparty": entry.counterparty,
            "contract_id": entry.contract_id,
            "contract_number": None,
            "amount": _money(remaining),
            "due_date": due.isoformat() if due else None,
            "days_left": days_left,
            "level": _level(days_left),
            "_amount_dec": remaining,
        })

    # b) pay_plan
    planned_entries = (
        db.query(CashFlowEntry)
        .filter(CashFlowEntry.project_id == project_id)
        .filter(CashFlowEntry.direction == "outflow")
        .filter(CashFlowEntry.status.notin_(_EXCLUDED_STATUSES))
        .filter(CashFlowEntry.actual_date.is_(None))
        .all()
    )
    for entry in planned_entries:
        planned_amount = _to_decimal(entry.planned_amount)
        if planned_amount <= 0:
            continue
        due = entry.planned_date
        if due is None:
            continue
        if due < back_date or due > horizon_date:
            continue
        invoiced_linked = (
            db.query(func.coalesce(func.sum(CashFlowEntry.invoiced_amount), 0))
            .filter(CashFlowEntry.plan_entry_id == entry.id)
            .scalar()
        )
        amount = planned_amount - _to_decimal(invoiced_linked)
        if amount <= 0:
            continue
        days_left = _days_left(due, today)
        items.append({
            "kind": "pay_plan",
            "title": entry.title,
            "counterparty": entry.counterparty,
            "contract_id": entry.contract_id,
            "contract_number": None,
            "amount": _money(amount),
            "due_date": due.isoformat() if due else None,
            "days_left": days_left,
            "level": _level(days_left),
            "_amount_dec": amount,
        })

    # c) sign_act
    stages = (
        db.query(ContractStage)
        .filter(ContractStage.project_id == project_id)
        .all()
    )
    for stage in stages:
        has_act = (
            db.query(AcceptanceAct)
            .filter(AcceptanceAct.stage_id == stage.id)
            .filter(AcceptanceAct.status.in_(("signed", "paid")))
            .first()
        )
        if has_act is not None:
            continue
        due = stage.planned_finish
        if due is None:
            continue
        if due > horizon_date:
            continue
        title = f"Акт: {stage.stage_number}-й этап"
        object_name = getattr(stage, "object_name", None)
        if object_name:
            title = f"{title} · {object_name}"
        contract_number: Optional[str] = None
        counterparty: Optional[str] = None
        if stage.contract_id:
            contract = db.query(Contract).filter(Contract.id == stage.contract_id).first()
            if contract is not None:
                counterparty = contract.counterparty
                contract_number = contract.number
        amount = _to_decimal(stage.amount)
        days_left = _days_left(due, today)
        items.append({
            "kind": "sign_act",
            "title": title,
            "counterparty": counterparty,
            "contract_id": stage.contract_id,
            "contract_number": contract_number,
            "amount": _money(amount),
            "due_date": due.isoformat() if due else None,
            "days_left": days_left,
            "level": _level(days_left),
            "_amount_dec": amount,
        })

    # d) receive
    income_entries = (
        db.query(CashFlowEntry)
        .filter(CashFlowEntry.project_id == project_id)
        .filter(CashFlowEntry.direction == "inflow")
        .filter(CashFlowEntry.status.notin_(_EXCLUDED_STATUSES))
        .filter(CashFlowEntry.actual_date.is_(None))
        .all()
    )
    for entry in income_entries:
        planned_amount = _to_decimal(entry.planned_amount)
        if planned_amount <= 0:
            continue
        due = entry.planned_date
        if due is None:
            continue
        if due > horizon_date:
            continue
        days_left = _days_left(due, today)
        items.append({
            "kind": "receive",
            "title": entry.title,
            "counterparty": entry.counterparty,
            "contract_id": entry.contract_id,
            "contract_number": None,
            "amount": _money(planned_amount),
            "due_date": due.isoformat() if due else None,
            "days_left": days_left,
            "level": _level(days_left),
            "_amount_dec": planned_amount,
        })

    # Plan payments/receipts long past with no recorded fact are usually just not
    # marked — one summary line instead of a wall of red alarms. Invoices stay.
    STALE_AFTER_DAYS = 14
    stale = {"count": 0, "amount": Decimal("0"), "pay": 0, "receive": 0}
    fresh = []
    for item in items:
        if item["kind"] in ("pay_plan", "receive") and item["days_left"] is not None and item["days_left"] < -STALE_AFTER_DAYS:
            stale["count"] += 1
            stale["amount"] += item["_amount_dec"]
            stale["pay" if item["kind"] == "pay_plan" else "receive"] += 1
        else:
            fresh.append(item)
    items = fresh

    # Sort: days_left ascending, None last
    items.sort(key=lambda x: (x["days_left"] is None, x["days_left"] if x["days_left"] is not None else 0))

    counts: Dict[str, int] = {"overdue": 0, "critical": 0, "soon": 0, "near": 0, "ok": 0, "none": 0}
    to_pay = Decimal("0")
    to_receive = Decimal("0")
    for item in items:
        counts[item["level"]] = counts.get(item["level"], 0) + 1
        days_left = item["days_left"]
        if item["kind"] in ("pay_invoice", "pay_plan"):
            if days_left is not None and days_left <= horizon_days:
                to_pay += item["_amount_dec"]
        if item["kind"] == "receive":
            to_receive += item["_amount_dec"]

    public_items = []
    for item in items:
        public_items.append({k: v for k, v in item.items() if not k.startswith("_")})

    return {
        "items": public_items[:100],
        "counts": counts,
        "totals": {
            "to_pay": _money(to_pay),
            "to_receive": _money(to_receive),
        },
        "unmarked": {
            "count": stale["count"], "amount": _money(stale["amount"]),
            "payments": stale["pay"], "receipts": stale["receive"],
        },
    }
