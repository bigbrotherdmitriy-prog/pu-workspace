from __future__ import annotations

from collections import OrderedDict
from datetime import date
from decimal import Decimal
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.execution_finance import BudgetLine, CashFlowEntry, CostCategory
from app.models.project import Project
from app.models.user import User

router = APIRouter(prefix="/projects", tags=["budget-from-dds"])

EXCLUDED_STATUSES = ("cancelled", "deleted", "rejected")
ZERO = Decimal("0.00")

ADDITIONAL_COST_CATEGORY_NAMES = ("дополнительные расходы", "дополнительные затраты")


def _is_additional_cost(
    *,
    category_name: str | None,
    fallback_category: str | None,
) -> bool:
    for value in (category_name, fallback_category):
        if value is not None and value.strip().lower() in ADDITIONAL_COST_CATEGORY_NAMES:
            return True
    return False


def _merge_invoice_parts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A part-paid invoice is split into the paid row and a remainder row (same
    source document and title). Show it as one invoice: summed invoiced/paid/left,
    the open part's entry for the next payment, status «partially_paid»."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    order: list[tuple] = []
    for row in rows:
        base_title = re.sub(r"\s*[—-]\s*остаток\s*$", "", (row.get("title") or "").strip())
        key = (row.get("document_id") or f"entry:{row['entry_id']}", base_title, row.get("counterparty"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    merged: list[dict[str, Any]] = []
    for key in order:
        parts = groups[key]
        if len(parts) == 1:
            merged.append(parts[0])
            continue
        invoiced = sum((Decimal(p["invoiced"]) for p in parts), ZERO)
        paid = sum((Decimal(p["paid"]) for p in parts), ZERO)
        left = invoiced - paid
        open_parts = [p for p in parts if Decimal(p["remaining_to_pay"]) > ZERO]
        target = open_parts[0] if open_parts else parts[0]
        actual_dates = [p["actual_date"] for p in parts if p.get("actual_date")]
        planned_dates = [p["planned_date"] for p in parts if p.get("planned_date")]
        merged.append({
            **target,
            "title": re.sub(r"\s*[—-]\s*остаток\s*$", "", (target.get("title") or "").strip()),
            "invoiced": _money(invoiced),
            "paid": _money(paid),
            "remaining_to_pay": _money(left),
            "planned_date": min(planned_dates) if planned_dates else None,
            "actual_date": max(actual_dates) if actual_dates else None,
            "status": "paid" if left <= ZERO else ("partially_paid" if paid > ZERO else target["status"]),
            "parts": len(parts),
        })
    return merged


def _money(value: Decimal | None) -> str:
    if value is None:
        value = ZERO
    return f"{value.quantize(Decimal('0.01')):.2f}"


class BudgetLineCategoryUpdate(BaseModel):
    cost_category_id: int


class RecordPaymentRequest(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)
    percent: Decimal | None = Field(default=None, gt=0, le=100)
    actual_date: date

    @field_validator("amount", "percent", mode="before")
    @classmethod
    def _money_before(cls, value: Any) -> Any:
        if value is None:
            return None
        return Decimal(str(value))

    @field_validator("amount", "percent")
    @classmethod
    def _exactly_one(cls, value: Decimal | None, info: Any) -> Decimal | None:
        return value


def _check_contract_limit(
    db: Session,
    *,
    contract_id: int | None,
    direction: str,
    additional_amount: Decimal,
) -> None:
    if contract_id is None or direction != "outflow":
        return

    from app.models.organization_contract import Contract

    contract = db.get(Contract, contract_id)
    if contract is None:
        return
    contract_amount = getattr(contract, "amount", None)
    if contract_amount is None or contract_amount <= ZERO:
        return

    already_paid = db.scalar(
        select(func.coalesce(func.sum(CashFlowEntry.actual_amount), 0)).where(
            CashFlowEntry.contract_id == contract_id,
            CashFlowEntry.direction == "outflow",
            CashFlowEntry.status == "paid",
        )
    )
    already_paid = Decimal(already_paid or 0)
    projected = already_paid + additional_amount
    if projected > contract_amount:
        over = projected - contract_amount
        number = getattr(contract, "number", "") or ""
        raise HTTPException(
            422,
            f"Оплаты по договору {number} превысят его сумму {_money(contract_amount)} "
            f"на {_money(over)} ₽",
        )


def _parse_month(value: str, field: str) -> date:
    try:
        year_str, month_str = value.split("-", 1)
        year = int(year_str)
        month = int(month_str)
    except (ValueError, AttributeError):
        raise HTTPException(422, f"Invalid {field}: expected YYYY-MM")
    if len(year_str) != 4 or len(month_str) != 2 or month < 1 or month > 12:
        raise HTTPException(422, f"Invalid {field}: expected YYYY-MM")
    return date(year, month, 1)


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _next_month(value: str) -> str:
    year, month = int(value[:4]), int(value[5:7])
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


class _Bucket:
    __slots__ = ("plan", "fact")

    def __init__(self) -> None:
        self.plan = ZERO
        self.fact = ZERO

    def add_plan(self, amount: Decimal) -> None:
        self.plan = self.plan + amount

    def add_fact(self, amount: Decimal) -> None:
        self.fact = self.fact + amount


@router.get("/{project_id}/budget-from-dds")
def budget_from_dds(
    project_id: int,
    contract_id: int | None = Query(default=None),
    month_from: str | None = Query(default=None),
    month_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict[str, Any]:
    require_project_role(db, user, project_id, "viewer")

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    currency = (getattr(project, "currency", None) or "RUB") or "RUB"

    from_month: date | None = _parse_month(month_from, "month_from") if month_from else None
    to_month: date | None = _parse_month(month_to, "month_to") if month_to else None
    if from_month and to_month and from_month > to_month:
        raise HTTPException(422, "month_from must be <= month_to")

    statement = select(CashFlowEntry).where(
        CashFlowEntry.project_id == project_id,
        CashFlowEntry.status.notin_(EXCLUDED_STATUSES),
    )
    if contract_id is not None:
        statement = statement.where(CashFlowEntry.contract_id == contract_id)

    entries = list(db.scalars(statement).all())

    category_names: dict[int, str] = {}
    category_ids = {e.cost_category_id for e in entries if e.cost_category_id is not None}
    if category_ids:
        rows = db.execute(
            select(CostCategory.id, CostCategory.name).where(CostCategory.id.in_(category_ids))
        ).all()
        category_names = {row[0]: row[1] for row in rows}

    budget_lines: list[BudgetLine] = []
    if entries:
        lines_statement = select(BudgetLine).where(BudgetLine.project_id == project_id)
        if contract_id is not None:
            lines_statement = lines_statement.where(BudgetLine.contract_id == contract_id)
        lines_statement = lines_statement.order_by(BudgetLine.id)
        budget_lines = list(db.scalars(lines_statement).all())

    budget_line_categories: dict[int, str] = {}
    line_category_ids = {line.cost_category_id for line in budget_lines if line.cost_category_id is not None}
    if line_category_ids:
        rows = db.execute(
            select(CostCategory.id, CostCategory.name).where(CostCategory.id.in_(line_category_ids))
        ).all()
        budget_line_categories = {row[0]: row[1] for row in rows}

    entries_by_line: dict[int, list[CashFlowEntry]] = {}
    for entry in entries:
        if entry.budget_line_id is None:
            continue
        entries_by_line.setdefault(entry.budget_line_id, []).append(entry)

    income = _Bucket()
    expense = _Bucket()
    direct_expense = _Bucket()
    additional_expense = _Bucket()
    by_category: "OrderedDict[tuple[int | None, str], dict[str, Any]]" = OrderedDict()
    months: "OrderedDict[str, _Bucket]" = OrderedDict()
    additional_items: list[dict[str, Any]] = []
    invoiced_totals = {"expense": ZERO, "direct": ZERO, "additional": ZERO}
    other_currency_count = 0

    for entry in entries:
        if (entry.currency or "RUB") != currency:
            other_currency_count += 1
            continue

        planned_amount = entry.planned_amount or ZERO
        actual_amount = entry.actual_amount or ZERO
        # Confirmed invoices are fact «выставлено» (owner, 15.10.2026).
        invoiced_amount = getattr(entry, "invoiced_amount", None) or ZERO

        planned_date = entry.planned_date
        actual_date = entry.actual_date

        plan_in_range = planned_date is not None and (
            (from_month is None or planned_date >= from_month)
            and (to_month is None or _month_key(planned_date) <= _month_key(to_month))
        )
        fact_in_range = actual_date is not None and (
            (from_month is None or actual_date >= from_month)
            and (to_month is None or _month_key(actual_date) <= _month_key(to_month))
        )

        is_income = entry.direction == "inflow"
        is_expense = entry.direction == "outflow"

        category_key_name: str | None = None
        if entry.cost_category_id is not None:
            category_key_name = category_names.get(entry.cost_category_id, "Без статьи")
        else:
            category_key_name = entry.category or "Без статьи"

        is_additional = is_expense and _is_additional_cost(
            category_name=category_names.get(entry.cost_category_id) if entry.cost_category_id is not None else None,
            fallback_category=entry.category,
        )

        if is_income:
            if plan_in_range:
                income.add_plan(planned_amount)
            if fact_in_range:
                income.add_fact(actual_amount)
        elif is_expense:
            if is_additional:
                if plan_in_range:
                    additional_expense.add_plan(planned_amount)
                if fact_in_range:
                    additional_expense.add_fact(actual_amount)
            else:
                if plan_in_range:
                    direct_expense.add_plan(planned_amount)
                if fact_in_range:
                    direct_expense.add_fact(actual_amount)
            if plan_in_range:
                expense.add_plan(planned_amount)
            if fact_in_range:
                expense.add_fact(actual_amount)
        else:
            continue

        if is_expense and plan_in_range and invoiced_amount:
            invoiced_totals["expense"] += invoiced_amount
            invoiced_totals["additional" if is_additional else "direct"] += invoiced_amount

        if is_additional and (plan_in_range or fact_in_range):
            additional_items.append({
                "entry_id": entry.id,
                "note": entry.note,
                "title": entry.title,
                "counterparty": entry.counterparty,
                "planned_date": None if planned_date is None else planned_date.isoformat(),
                "actual_date": None if actual_date is None else actual_date.isoformat(),
                "fact": _money(actual_amount if fact_in_range else ZERO),
                "invoiced": _money(invoiced_amount if plan_in_range else ZERO),
                "_sort_fact": max(actual_amount if fact_in_range else ZERO, invoiced_amount if plan_in_range else ZERO),
            })

        key: tuple[int | None, str]
        if entry.cost_category_id is not None:
            key = (entry.cost_category_id, category_names.get(entry.cost_category_id, "Без статьи"))
        else:
            key = (None, entry.category or "Без статьи")

        if is_expense and not is_additional:
            bucket = by_category.get(key)
            if bucket is None:
                bucket = {
                    "category_id": key[0],
                    "category": key[1],
                    "plan": ZERO,
                    "fact": ZERO,
                    "invoiced": ZERO,
                    "counterparties": OrderedDict(),
                }
                by_category[key] = bucket
            if plan_in_range:
                bucket["plan"] = bucket["plan"] + planned_amount
            if fact_in_range:
                bucket["fact"] = bucket["fact"] + actual_amount
            if plan_in_range:
                bucket["invoiced"] = bucket["invoiced"] + invoiced_amount
            counterparty_name = entry.counterparty or "—"
            cp_map = bucket["counterparties"]
            cp_bucket = cp_map.get(counterparty_name)
            if cp_bucket is None:
                cp_bucket = {"counterparty": counterparty_name, "plan": ZERO, "fact": ZERO}
                cp_map[counterparty_name] = cp_bucket
            if plan_in_range:
                cp_bucket["plan"] = cp_bucket["plan"] + planned_amount
            if fact_in_range:
                cp_bucket["fact"] = cp_bucket["fact"] + actual_amount

        if is_income:
            if plan_in_range:
                month_key = _month_key(planned_date)
                months.setdefault(month_key, _Bucket())
                months[month_key].add_plan(planned_amount)
            if fact_in_range:
                month_key = _month_key(actual_date)
                months.setdefault(month_key, _Bucket())
                months[month_key].add_fact(actual_amount)
        elif is_expense:
            if plan_in_range:
                month_key = _month_key(planned_date)
                months.setdefault(month_key, _Bucket())
                months[month_key].add_plan(-planned_amount)
            if fact_in_range:
                month_key = _month_key(actual_date)
                months.setdefault(month_key, _Bucket())
                months[month_key].add_fact(-actual_amount)

    income_plan = income.plan
    income_fact = income.fact
    additional_plan = additional_expense.plan
    additional_fact = additional_expense.fact
    direct_expense_plan = direct_expense.plan
    direct_expense_fact = direct_expense.fact
    expense_plan = expense.plan
    expense_fact = expense.fact
    profit_plan = income_plan - expense_plan
    profit_fact = income_fact - expense_fact

    def margin(profit: Decimal, income_value: Decimal) -> Decimal | None:
        if income_value == 0:
            return None
        return (profit / income_value * Decimal("100"))

    margin_plan = margin(profit_plan, income_plan)
    margin_fact = margin(profit_fact, income_fact)

    income_plan_str = _money(income_plan)
    income_fact_str = _money(income_fact)
    expense_plan_str = _money(expense_plan)
    expense_fact_str = _money(expense_fact)
    direct_expense_plan_str = _money(direct_expense_plan)
    direct_expense_fact_str = _money(direct_expense_fact)
    additional_plan_str = _money(additional_plan)
    additional_fact_str = _money(additional_fact)

    expense_by_article: list[dict[str, Any]] = []
    for line in budget_lines:
        line_entries = entries_by_line.get(line.id)
        if not line_entries:
            continue
        category_name = None
        if line.cost_category_id is not None:
            category_name = budget_line_categories.get(line.cost_category_id)
        if _is_additional_cost(category_name=category_name, fallback_category=line.category):
            continue

        plan_total = ZERO
        invoiced_total = ZERO
        paid_total = ZERO
        invoice_rows: list[dict[str, Any]] = []
        title: str | None = None
        fallback_title: str | None = None

        for entry in line_entries:
            planned_amount = entry.planned_amount or ZERO
            actual_amount = entry.actual_amount or ZERO
            invoiced_amount = getattr(entry, "invoiced_amount", None) or ZERO
            planned_date = entry.planned_date
            actual_date = entry.actual_date
            plan_in_range = planned_date is not None and (
                (from_month is None or planned_date >= from_month)
                and (to_month is None or _month_key(planned_date) <= _month_key(to_month))
            )
            fact_in_range = actual_date is not None and (
                (from_month is None or actual_date >= from_month)
                and (to_month is None or _month_key(actual_date) <= _month_key(to_month))
            )
            if fallback_title is None:
                fallback_title = entry.title
            if entry.entry_kind == "plan_forecast" and title is None:
                title = entry.title
            if plan_in_range:
                plan_total += planned_amount
                invoiced_total += invoiced_amount
            if fact_in_range:
                paid_total += actual_amount
            if invoiced_amount > ZERO:
                invoice_rows.append({
                    "entry_id": entry.id,
                    "title": entry.title,
                    "counterparty": entry.counterparty,
                    "invoiced": _money(invoiced_amount),
                    "paid": _money(actual_amount),
                    "remaining_to_pay": _money(invoiced_amount - actual_amount),
                    "planned_date": None if planned_date is None else planned_date.isoformat(),
                    "actual_date": None if actual_date is None else actual_date.isoformat(),
                    "status": entry.status,
                    "document_id": entry.source_document_id,
                })

        invoice_rows = _merge_invoice_parts(invoice_rows)
        expense_by_article.append({
            "budget_line_id": line.id,
            "title": title or fallback_title or line.category,
            "category_id": line.cost_category_id,
            "category": category_name or line.category,
            "plan": _money(plan_total),
            "invoiced": _money(invoiced_total),
            "paid": _money(paid_total),
            "remaining": _money(plan_total - invoiced_total),
            "invoices": invoice_rows,
        })

    sorted_additional = sorted(
        additional_items,
        key=lambda item: item["_sort_fact"],
        reverse=True,
    )[:200]
    additional_items_out = [
        {
            "entry_id": item["entry_id"],
            "note": item["note"],
            "title": item["title"],
            "counterparty": item["counterparty"],
            "planned_date": item["planned_date"],
            "actual_date": item["actual_date"],
            "fact": item["fact"],
            "invoiced": item["invoiced"],
        }
        for item in sorted_additional
    ]

    expense_by_category = []
    for bucket in by_category.values():
        deviation = bucket["fact"] - bucket["plan"]
        # Remaining against the plan: whichever is larger of invoiced and paid
        # is already committed (an invoice counts before it is paid).
        remaining = bucket["plan"] - max(bucket["invoiced"], bucket["fact"])
        counterparties = [
            {
                "counterparty": cp["counterparty"],
                "plan": _money(cp["plan"]),
                "fact": _money(cp["fact"]),
            }
            for cp in bucket["counterparties"].values()
        ]
        expense_by_category.append({
            "category_id": bucket["category_id"],
            "category": bucket["category"],
            "plan": _money(bucket["plan"]),
            "fact": _money(bucket["fact"]),
            "invoiced": _money(bucket["invoiced"]),
            "remaining": _money(remaining),
            "deviation": _money(deviation),
            "counterparties": counterparties,
        })

    month_plan_net = {m: b.plan for m, b in months.items()}
    month_fact_net = {m: b.fact for m, b in months.items()}

    month_income_plan: dict[str, Decimal] = {}
    month_income_fact: dict[str, Decimal] = {}
    month_expense_plan: dict[str, Decimal] = {}
    month_expense_fact: dict[str, Decimal] = {}
    for entry in entries:
        if (entry.currency or "RUB") != currency:
            continue
        planned_amount = entry.planned_amount or ZERO
        actual_amount = entry.actual_amount or ZERO
        planned_date = entry.planned_date
        actual_date = entry.actual_date
        plan_in_range = planned_date is not None and (
            (from_month is None or planned_date >= from_month)
            and (to_month is None or _month_key(planned_date) <= _month_key(to_month))
        )
        fact_in_range = actual_date is not None and (
            (from_month is None or actual_date >= from_month)
            and (to_month is None or _month_key(actual_date) <= _month_key(to_month))
        )
        if entry.direction == "inflow":
            if plan_in_range:
                mk = _month_key(planned_date)
                month_income_plan[mk] = month_income_plan.get(mk, ZERO) + planned_amount
            if fact_in_range:
                mk = _month_key(actual_date)
                month_income_fact[mk] = month_income_fact.get(mk, ZERO) + actual_amount
        elif entry.direction == "outflow":
            if plan_in_range:
                mk = _month_key(planned_date)
                month_expense_plan[mk] = month_expense_plan.get(mk, ZERO) + planned_amount
            if fact_in_range:
                mk = _month_key(actual_date)
                month_expense_fact[mk] = month_expense_fact.get(mk, ZERO) + actual_amount

    all_months = sorted(
        set(months.keys())
        | set(month_income_plan.keys())
        | set(month_income_fact.keys())
        | set(month_expense_plan.keys())
        | set(month_expense_fact.keys())
    )

    by_month = []
    cumulative_plan = ZERO
    cumulative_fact = ZERO
    for month_key in all_months:
        inc_plan = month_income_plan.get(month_key, ZERO)
        inc_fact = month_income_fact.get(month_key, ZERO)
        exp_plan = month_expense_plan.get(month_key, ZERO)
        exp_fact = month_expense_fact.get(month_key, ZERO)
        net_plan = inc_plan - exp_plan
        net_fact = inc_fact - exp_fact
        cumulative_plan = cumulative_plan + net_plan
        cumulative_fact = cumulative_fact + net_fact
        by_month.append({
            "month": month_key,
            "income_plan": _money(inc_plan),
            "income_fact": _money(inc_fact),
            "expense_plan": _money(exp_plan),
            "expense_fact": _money(exp_fact),
            "net_plan": _money(net_plan),
            "net_fact": _money(net_fact),
            "cumulative_plan": _money(cumulative_plan),
            "cumulative_fact": _money(cumulative_fact),
        })

    return {
        "currency": currency,
        "income": {"plan": income_plan_str, "fact": income_fact_str},
        "expense_total": {"plan": expense_plan_str, "fact": expense_fact_str,
                          "invoiced": _money(invoiced_totals["expense"])},
        "direct_expense_total": {"plan": direct_expense_plan_str, "fact": direct_expense_fact_str,
                                 "invoiced": _money(invoiced_totals["direct"])},
        "additional_costs": {
            "plan": additional_plan_str,
            "fact": additional_fact_str,
            "invoiced": _money(invoiced_totals["additional"]),
            "items": additional_items_out,
        },
        "expense_by_category": expense_by_category,
        "expense_by_article": expense_by_article,
        "profit": {"plan": _money(profit_plan), "fact": _money(profit_fact)},
        "margin_pct": {
            "plan": None if margin_plan is None else _money(margin_plan),
            "fact": None if margin_fact is None else _money(margin_fact),
        },
        "by_month": by_month,
        "other_currency_entries_count": other_currency_count,
    }


@router.patch("/{project_id}/budget-lines/{line_id}/category")
def update_budget_line_category(
    project_id: int,
    line_id: int,
    payload: BudgetLineCategoryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict[str, Any]:
    require_project_role(db, user, project_id, "manager")

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    line = db.get(BudgetLine, line_id)
    if not line or line.project_id != project_id:
        raise HTTPException(404, "Статья бюджета не найдена")

    category = db.get(CostCategory, payload.cost_category_id)
    if (
        not category
        or category.organization_id != project.organization_id
        or not getattr(category, "is_active", True)
    ):
        raise HTTPException(422, "Категория не найдена")

    old_category_id = line.cost_category_id
    line.cost_category_id = category.id

    db.execute(
        CashFlowEntry.__table__.update()
        .where(CashFlowEntry.budget_line_id == line.id)
        .values(cost_category_id=category.id, category=category.name)
    )

    from app.api.execution_finance import _audit

    _audit(
        db,
        "budget_line_category_changed",
        "budget_line",
        line.id,
        user.id,
        f"from={old_category_id}; to={category.id}",
    )
    db.commit()

    return {
        "budget_line_id": line.id,
        "cost_category_id": category.id,
        "category": category.name,
    }


class CashFlowNoteUpdate(BaseModel):
    note: str = Field(min_length=1, max_length=300)

    @field_validator("note", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


@router.patch("/{project_id}/cash-flow/{entry_id}/note")
def update_cash_flow_note(
    project_id: int,
    entry_id: int,
    payload: CashFlowNoteUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict[str, Any]:
    """Short editable description of an invoice row (used for additional costs)."""
    require_project_role(db, user, project_id, "manager")
    entry = db.get(CashFlowEntry, entry_id)
    if not entry or entry.project_id != project_id:
        raise HTTPException(404, "Запись ДДС не найдена")
    old = entry.note
    entry.note = payload.note

    from app.api.execution_finance import _audit

    _audit(db, "cash_flow_note_changed", "cash_flow", entry.id, user.id, f"from={old!r}; to={payload.note!r}")
    db.commit()
    return {"id": entry.id, "note": entry.note}


class RecordFactRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    actual_date: date
    note: str | None = None
    # Income document (our act / invoice / КС-3) the money arrived for.
    basis_entry_id: int | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_before(cls, value: Any) -> Any:
        return Decimal(str(value))


@router.post("/{project_id}/cash-flow/{entry_id}/record-fact")
def record_plan_fact(
    project_id: int,
    entry_id: int,
    payload: RecordFactRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict[str, Any]:
    require_project_role(db, user, project_id, "manager")

    plan = db.get(CashFlowEntry, entry_id)
    if not plan or plan.project_id != project_id:
        raise HTTPException(404, "Запись ДДС не найдена")

    if (plan.planned_amount or ZERO) <= ZERO or plan.status in EXCLUDED_STATUSES:
        raise HTTPException(422, "Это не строка плана")

    already_fact = db.scalar(
        select(func.coalesce(func.sum(CashFlowEntry.actual_amount), 0)).where(
            CashFlowEntry.plan_entry_id == plan.id,
            CashFlowEntry.actual_date.is_not(None),
        )
    )
    already_fact = Decimal(already_fact or 0)
    remaining = (plan.planned_amount or ZERO) - already_fact
    if payload.amount > remaining + Decimal("0.01"):
        raise HTTPException(422, f"Больше остатка по строке плана: {_money(remaining)}")

    basis = None
    if payload.basis_entry_id is not None:
        basis = db.get(CashFlowEntry, payload.basis_entry_id)
        if basis is None or basis.project_id != project_id or basis.plan_entry_id != plan.id:
            raise HTTPException(422, "Документ-основание не относится к этой строке плана")
    status = "received" if plan.direction == "inflow" else "paid"
    new_entry = CashFlowEntry(
        entry_kind="fact",
        project_id=plan.project_id,
        contract_id=plan.contract_id,
        direction=plan.direction,
        title=plan.title,
        category=plan.category,
        cost_category_id=plan.cost_category_id,
        budget_line_id=plan.budget_line_id,
        object_name=plan.object_name,
        counterparty=plan.counterparty,
        currency=plan.currency,
        planned_date=payload.actual_date,
        planned_amount=ZERO,
        actual_amount=payload.amount,
        actual_date=payload.actual_date,
        invoiced_amount=ZERO,
        status=status,
        plan_entry_id=plan.id,
        source_name="plan_fact",
        note=payload.note or (basis.title if basis is not None else None),
    )
    db.add(new_entry)
    if basis is not None:
        # The document stays «выставлено»; it is now settled.
        basis.status = "received"
        basis.actual_date = payload.actual_date
    db.flush()

    from app.api.execution_finance import _audit

    _audit(
        db,
        "plan_fact_recorded",
        "cash_flow",
        new_entry.id,
        user.id,
        f"plan={plan.id}; amount={payload.amount}; date={payload.actual_date}",
    )
    db.commit()

    return {
        "id": new_entry.id,
        "plan_entry_id": plan.id,
        "actual_amount": _money(payload.amount),
        "actual_date": payload.actual_date.isoformat(),
        "plan_remaining": _money(remaining - payload.amount),
    }


@router.post("/{project_id}/cash-flow/{entry_id}/record-payment")
def record_cash_flow_payment(
    project_id: int,
    entry_id: int,
    payload: RecordPaymentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> Any:
    require_project_role(db, user, project_id, "manager")

    entry = db.get(CashFlowEntry, entry_id)
    if not entry or entry.project_id != project_id:
        raise HTTPException(404, "Запись ДДС не найдена")

    if entry.direction != "outflow" or (entry.invoiced_amount or ZERO) <= ZERO:
        raise HTTPException(422, "Это не счёт")

    if (payload.amount is None) == (payload.percent is None):
        raise HTTPException(422, "Укажите сумму или процент")

    invoiced_amount = entry.invoiced_amount or ZERO
    if payload.amount is not None:
        payment_amount = Decimal(payload.amount)
    else:
        payment_amount = (invoiced_amount * Decimal(payload.percent) / Decimal("100")).quantize(
            Decimal("0.01")
        )

    if payment_amount > invoiced_amount:
        raise HTTPException(422, "Оплата больше суммы счёта")

    _check_contract_limit(
        db,
        contract_id=entry.contract_id,
        direction=entry.direction,
        additional_amount=payment_amount,
    )

    remainder_entry_id: int | None = None
    if payment_amount < invoiced_amount:
        remainder_entry = CashFlowEntry(
            entry_kind=entry.entry_kind or "legacy_unclassified",
            # A remainder is an invoice tranche, never a matrix forecast cell.
            matrix_article_id=None,
            matrix_month=None,
            matrix_operation_id=None,
            project_id=entry.project_id,
            contract_id=entry.contract_id,
            schedule_item_id=entry.schedule_item_id,
            budget_line_id=entry.budget_line_id,
            task_id=entry.task_id,
            source_document_id=entry.source_document_id,
            source_document_version_id=entry.source_document_version_id,
            source_document_sha256=entry.source_document_sha256,
            cost_category_id=entry.cost_category_id,
            direction=entry.direction,
            title=re.sub(r"\s*[—-]\s*остаток\s*$", "", (entry.title or "").strip()) + " — остаток",
            planned_date=entry.planned_date,
            actual_date=None,
            planned_amount=ZERO,
            actual_amount=ZERO,
            invoiced_amount=invoiced_amount - payment_amount,
            plan_entry_id=entry.plan_entry_id,
            currency=entry.currency,
            counterparty=entry.counterparty,
            object_name=entry.object_name,
            category=entry.category,
            note=entry.note,
            status="approved",
            source_name=entry.source_name,
            source_excerpt=entry.source_excerpt,
        )
        db.add(remainder_entry)
        db.flush()
        remainder_entry_id = remainder_entry.id
        entry.invoiced_amount = payment_amount
        db.flush()

    if entry.status == "proposed":
        entry.status = "approved"

    from app.api.execution_finance import PaymentConfirmation, confirm_payment

    result = confirm_payment(
        entry_id,
        PaymentConfirmation(actual_amount=payment_amount, actual_date=payload.actual_date),
        db=db,
        user=user,
    )
    if isinstance(result, dict):
        result["remainder_entry_id"] = remainder_entry_id
    return result
