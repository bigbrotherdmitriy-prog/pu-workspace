from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cash_gap import plan_cash_gaps
from app.core.auth import require_user
from app.database import get_db
from app.models.execution_finance import CashFlowEntry, InvoiceExtractionProposal
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

_SKIP_STATUSES = ("cancelled", "deleted", "rejected")
_OVERDUE_STATUSES = ("approved", "proposed")
_DECIMAL_ZERO = Decimal("0")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@router.get("")
def get_portfolio(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    today = date.today()

    stmt = select(Project).where(Project.archived_at.is_(None))
    if not user.is_admin:
        stmt = stmt.join(
            ProjectMember, ProjectMember.project_id == Project.id
        ).where(ProjectMember.user_id == user.id)
    stmt = stmt.order_by(Project.id)
    projects = list(db.execute(stmt).scalars().all())

    project_ids = [p.id for p in projects]

    entries_by_project: dict[int, list[CashFlowEntry]] = {pid: [] for pid in project_ids}
    invoices_by_project: dict[int, int] = {pid: 0 for pid in project_ids}

    if project_ids:
        entries_stmt = select(CashFlowEntry).where(
            CashFlowEntry.project_id.in_(project_ids)
        )
        for row in db.execute(entries_stmt).scalars().all():
            entries_by_project.setdefault(row.project_id, []).append(row)

        invoice_counts_stmt = (
            select(
                InvoiceExtractionProposal.project_id,
                func.count(InvoiceExtractionProposal.id),
            )
            .where(
                InvoiceExtractionProposal.project_id.in_(project_ids),
                InvoiceExtractionProposal.status == "proposed",
            )
            .group_by(InvoiceExtractionProposal.project_id)
        )
        for pid, cnt in db.execute(invoice_counts_stmt).all():
            invoices_by_project[pid] = int(cnt)

    items: list[dict] = []
    for project in projects:
        entries = entries_by_project.get(project.id, [])
        cash = [e for e in entries if e.status not in _SKIP_STATUSES]

        plan_inflow = _DECIMAL_ZERO
        plan_outflow = _DECIMAL_ZERO
        received_inflow = _DECIMAL_ZERO
        paid_outflow = _DECIMAL_ZERO
        overdue_count = 0
        overdue_amount = _DECIMAL_ZERO

        for row in cash:
            planned_amount = row.planned_amount or _DECIMAL_ZERO
            actual_amount = row.actual_amount or _DECIMAL_ZERO
            if row.direction == "inflow":
                plan_inflow += planned_amount
                if row.actual_date is not None:
                    received_inflow += actual_amount
            elif row.direction == "outflow":
                plan_outflow += planned_amount
                if row.actual_date is not None:
                    paid_outflow += actual_amount
                if (
                    row.planned_date is not None
                    and row.planned_date < today
                    and row.actual_date is None
                    and row.status in _OVERDUE_STATUSES
                ):
                    # Only an issued invoice can be overdue; an unpaid plan line is just plan.
                    invoiced = getattr(row, "invoiced_amount", None) or _DECIMAL_ZERO
                    if invoiced > 0:
                        overdue_count += 1
                        overdue_amount += invoiced

        gap = plan_cash_gaps(cash)
        worst = gap["worst"]

        invoices_to_confirm = invoices_by_project.get(project.id, 0)

        if worst is not None or overdue_count > 0:
            status = "risk"
        elif invoices_to_confirm > 0:
            status = "attention"
        else:
            status = "ok"

        items.append({
            "project_id": project.id,
            "name": project.name,
            "currency": project.currency or "RUB",
            "plan_inflow": _money(plan_inflow),
            "plan_outflow": _money(plan_outflow),
            "received_inflow": _money(received_inflow),
            "paid_outflow": _money(paid_outflow),
            "overdue_count": overdue_count,
            "overdue_amount": _money(overdue_amount),
            "invoices_to_confirm": invoices_to_confirm,
            "cash_gap_month": worst["month"] if worst else None,
            "cash_gap_amount": worst["cumulative"] if worst else None,
            "cash_gap_explanation": gap["explanation"],
            "status": status,
        })

    _order = {"risk": 0, "attention": 1, "ok": 2}
    items.sort(key=lambda it: (_order.get(it["status"], 3), it["name"] or ""))

    total_overdue = _DECIMAL_ZERO
    total_invoices = 0
    risk_count = 0
    attention_count = 0
    for it in items:
        total_overdue += Decimal(it["overdue_amount"])
        total_invoices += it["invoices_to_confirm"]
        if it["status"] == "risk":
            risk_count += 1
        elif it["status"] == "attention":
            attention_count += 1

    return {
        "projects": items,
        "totals": {
            "projects": len(items),
            "risk": risk_count,
            "attention": attention_count,
            "overdue_amount": _money(total_overdue),
            "invoices_to_confirm": total_invoices,
        },
    }
