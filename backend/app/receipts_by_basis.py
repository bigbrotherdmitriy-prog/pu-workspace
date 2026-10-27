from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Contract


EXCLUDED_STATUSES = ("cancelled", "deleted", "rejected")
ZERO = Decimal("0.00")


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def basis_kind(title: Optional[str], source_name: Optional[str]) -> str:
    lowered = (title or "").strip().lower()
    source = (source_name or "").strip().lower()
    compact = lowered.replace(" ", "")
    if "кс-3" in lowered or "кс3" in compact or "справка о стоимости" in lowered:
        return "КС-3"
    if "кс-2" in lowered or "кс2" in compact:
        return "КС-2"
    if "упд" in lowered:
        return "УПД"
    if source.startswith("acceptance_act:") or "акт" in lowered:
        return "Акт"
    if "счет" in lowered or "счёт" in lowered or "оферт" in lowered:
        return "Счёт"
    return "Документ"


def basis_amount(entry: CashFlowEntry) -> Decimal:
    invoiced = _to_decimal(entry.invoiced_amount)
    if invoiced > ZERO:
        return invoiced
    return _to_decimal(entry.planned_amount)


def _sum_facts(db: Session, entry_id: int) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(CashFlowEntry.actual_amount), 0)).where(
            CashFlowEntry.plan_entry_id == entry_id,
            CashFlowEntry.actual_date.is_not(None),
        )
    )
    return Decimal(total or 0)


def _basis_received(db: Session, entry: CashFlowEntry, amount: Decimal) -> Decimal:
    received = _sum_facts(db, entry.id)
    if received == ZERO and entry.status == "received":
        return amount
    return received


def _is_basis(entry: CashFlowEntry) -> bool:
    if entry.direction != "inflow":
        return False
    if entry.status in EXCLUDED_STATUSES:
        return False
    source_name = entry.source_name or ""
    if source_name == "receipt_fact":
        return False
    invoiced = _to_decimal(entry.invoiced_amount)
    if invoiced > ZERO:
        return True
    if source_name.startswith("acceptance_act:"):
        return True
    return False


def _contract_groups(db: Session, project_id: int) -> List[Dict[str, Any]]:
    contracts = list(
        db.scalars(
            select(Contract)
            .where(Contract.project_id == project_id)
            .where(Contract.contract_kind == "customer")
            .order_by(Contract.id)
        ).all()
    )
    groups: List[Dict[str, Any]] = [
        {
            "contract_id": c.id,
            "number": c.number,
            "counterparty": c.counterparty,
            "documents": [],
        }
        for c in contracts
    ]
    if not groups:
        groups.append({
            "contract_id": None,
            "number": None,
            "counterparty": None,
            "documents": [],
        })
    return groups


def receipts_by_basis(
    db: Session,
    project_id: int,
    today: date,
) -> Dict[str, Any]:
    groups = _contract_groups(db, project_id)
    by_contract: Dict[Optional[int], Dict[str, Any]] = {g["contract_id"]: g for g in groups}
    fallback = by_contract.get(None)
    customer_groups = [g for g in groups if g["contract_id"] is not None]

    entries = list(
        db.scalars(select(CashFlowEntry).where(CashFlowEntry.project_id == project_id)).all()
    )

    for entry in entries:
        if not _is_basis(entry):
            continue
        amount = basis_amount(entry)
        if amount <= ZERO:
            continue
        received = _basis_received(db, entry, amount)
        remaining = amount - received
        if remaining < ZERO:
            remaining = ZERO
        if remaining <= Decimal("0.009"):
            state = "received"
        elif entry.planned_date is not None and entry.planned_date < today:
            state = "overdue"
        else:
            state = "expected"
        group = by_contract.get(entry.contract_id)
        if group is None and len(customer_groups) == 1:
            # One customer contract: a document without a contract belongs to it (never asked of the user).
            group = customer_groups[0]
        if group is None:
            if fallback is None:
                fallback = {
                    "contract_id": None,
                    "number": None,
                    "counterparty": None,
                    "documents": [],
                }
                by_contract[None] = fallback
                groups.append(fallback)
            group = fallback
        group["documents"].append({
            "id": entry.id,
            "title": entry.title,
            "kind": basis_kind(entry.title, entry.source_name),
            "date": entry.planned_date.isoformat() if entry.planned_date else None,
            "amount": _money(amount),
            "received": _money(received),
            "remaining": _money(remaining),
            "state": state,
            "source_document_id": entry.source_document_id,
            "_sort_date": entry.planned_date or date(1900, 1, 1),
        })

    total_invoiced = ZERO
    total_received = ZERO
    total_remaining = ZERO
    contracts_out: List[Dict[str, Any]] = []
    for group in groups:
        docs = sorted(
            group["documents"],
            key=lambda d: (d["_sort_date"], d["id"]),
        )
        invoiced = ZERO
        received = ZERO
        remaining = ZERO
        public_docs: List[Dict[str, Any]] = []
        for doc in docs:
            invoiced += Decimal(doc["amount"])
            received += Decimal(doc["received"])
            remaining += Decimal(doc["remaining"])
            public_docs.append({k: v for k, v in doc.items() if not k.startswith("_")})
        total_invoiced += invoiced
        total_received += received
        total_remaining += remaining
        contracts_out.append({
            "contract_id": group["contract_id"],
            "number": group["number"],
            "counterparty": group["counterparty"],
            "documents": public_docs,
            "totals": {
                "invoiced": _money(invoiced),
                "received": _money(received),
                "remaining": _money(remaining),
            },
        })

    return {
        "contracts": contracts_out,
        "totals": {
            "invoiced": _money(total_invoiced),
            "received": _money(total_received),
            "remaining": _money(total_remaining),
        },
    }


def record_receipt(
    db: Session,
    project_id: int,
    entry_id: int,
    amount: Decimal,
    actual_date: date,
    user_id: int,
) -> Dict[str, Any]:
    basis = db.get(CashFlowEntry, entry_id)
    if basis is None or basis.project_id != project_id:
        raise HTTPException(404, "Документ не найден")
    if basis.direction != "inflow":
        raise HTTPException(422, "Это не документ на приход")
    if basis.source_name == "receipt_fact":
        raise HTTPException(422, "Это не документ на приход")
    basis_amt = basis_amount(basis)
    if basis_amt <= ZERO:
        raise HTTPException(422, "Это не документ на приход")

    received = _basis_received(db, basis, basis_amt)
    remaining = basis_amt - received
    if remaining < ZERO:
        remaining = ZERO
    if amount > remaining + Decimal("0.01"):
        raise HTTPException(422, f"Больше остатка по документу: {_money(remaining)}")

    fact = CashFlowEntry(
        entry_kind="fact",
        project_id=basis.project_id,
        contract_id=basis.contract_id,
        direction="inflow",
        title=basis.title,
        category=basis.category,
        cost_category_id=basis.cost_category_id,
        budget_line_id=basis.budget_line_id,
        object_name=basis.object_name,
        counterparty=basis.counterparty,
        currency=basis.currency,
        planned_date=actual_date,
        planned_amount=ZERO,
        actual_amount=amount,
        actual_date=actual_date,
        invoiced_amount=ZERO,
        status="received",
        plan_entry_id=basis.id,
        source_name="receipt_fact",
        note="Получено по: " + (basis.title or ""),
    )
    db.add(fact)
    db.flush()

    new_received = received + amount
    new_remaining = basis_amt - new_received
    if new_remaining < ZERO:
        new_remaining = ZERO
    if new_remaining <= Decimal("0.009"):
        basis.status = "received"
        basis.actual_date = actual_date
        db.flush()

    from app.api.execution_finance import _audit

    _audit(
        db,
        "receipt_recorded",
        "cash_flow",
        fact.id,
        user_id,
        f"basis={basis.id}; amount={amount}; date={actual_date}",
    )

    return {
        "id": fact.id,
        "basis_entry_id": basis.id,
        "amount": _money(amount),
        "actual_date": actual_date.isoformat(),
        "remaining": _money(new_remaining),
    }
