from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.finance_money import from_minor_units, money, to_minor_units
from app.models.execution_finance import (
    AcceptanceAct,
    BudgetLine,
    CashFlowEntry,
    CashFlowPlanMutation,
    ContractBudgetProposal,
    InvoiceExtractionProposal,
    PaymentEvent,
    ProcurementItem,
)
from app.models.management import Obligation
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.task import Task


_AMOUNT_FIELDS = (
    (Contract, ("amount", "advance_amount")),
    (BudgetLine, ("planned_amount", "committed_amount", "actual_amount", "forecast_amount")),
    (CashFlowEntry, ("planned_amount", "actual_amount")),
    (CashFlowPlanMutation, ("previous_planned_amount", "planned_amount")),
    (InvoiceExtractionProposal, ("amount",)),
    (ContractBudgetProposal, ("amount", "advance_amount")),
    (ProcurementItem, ("planned_amount", "actual_amount")),
    (AcceptanceAct, ("amount",)),
    (PaymentEvent, ("amount",)),
    (Obligation, ("amount",)),
    (Task, ("amount",)),
)

_CURRENCY_MODELS = (
    BudgetLine,
    CashFlowEntry,
    InvoiceExtractionProposal,
    ContractBudgetProposal,
    ProcurementItem,
    AcceptanceAct,
    PaymentEvent,
)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def build_money_dry_run_report(
    db: Session,
    *,
    project_id: int,
    expected_currency: str = "RUB",
) -> dict:
    """Build a read-only before/after report for a project money migration."""
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Проект {project_id} не найден")

    rows: list[dict] = []
    changed = 0
    max_abs_delta = Decimal("0.00")
    for model, fields in _AMOUNT_FIELDS:
        for item in db.scalars(select(model).where(model.project_id == project_id)):
            for field in fields:
                raw = getattr(item, field)
                if raw is None:
                    continue
                before = Decimal(raw)
                normalized = money(before)
                minor = to_minor_units(normalized)
                after = from_minor_units(minor)
                delta = after - before
                if delta:
                    changed += 1
                max_abs_delta = max(max_abs_delta, abs(delta))
                rows.append({
                    "table": model.__tablename__,
                    "id": item.id,
                    "field": field,
                    "before": _decimal_text(before),
                    "minor_units": str(minor),
                    "after": _decimal_text(after),
                    "delta": _decimal_text(delta),
                })

    currency_mismatches: list[dict] = []
    for model in _CURRENCY_MODELS:
        for item in db.scalars(select(model).where(model.project_id == project_id)):
            if item.currency != expected_currency:
                currency_mismatches.append({
                    "table": model.__tablename__,
                    "id": item.id,
                    "currency": item.currency,
                    "expected_currency": expected_currency,
                })

    report = {
        "project_id": project.id,
        "project_name": project.name,
        "expected_currency": expected_currency,
        "value_count": len(rows),
        "changed_value_count": changed,
        "max_abs_delta": _decimal_text(max_abs_delta),
        "currency_mismatch_count": len(currency_mismatches),
        "currency_mismatches": currency_mismatches,
        "rows": rows,
    }
    report["status"] = (
        "PASS"
        if changed == 0 and not currency_mismatches
        else "STOP"
    )
    return report


def assert_money_dry_run_safe(report: dict) -> None:
    max_delta = Decimal(str(report.get("max_abs_delta", "0")))
    changed = int(report.get("changed_value_count", 0))
    mismatches = int(report.get("currency_mismatch_count", 0))
    if changed or max_delta or mismatches:
        raise RuntimeError(
            "V6-00a money dry-run STOP: "
            f"changed={changed}; max_abs_delta={max_delta:.2f}; "
            f"currency_mismatches={mismatches}"
        )
