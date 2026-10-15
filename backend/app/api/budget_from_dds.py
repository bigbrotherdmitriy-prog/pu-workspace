from __future__ import annotations

from collections import OrderedDict
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.execution_finance import CashFlowEntry, CostCategory
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


def _money(value: Decimal | None) -> str:
    if value is None:
        value = ZERO
    return f"{value.quantize(Decimal('0.01')):.2f}"


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

    sorted_additional = sorted(
        additional_items,
        key=lambda item: item["_sort_fact"],
        reverse=True,
    )[:200]
    additional_items_out = [
        {
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
        "profit": {"plan": _money(profit_plan), "fact": _money(profit_fact)},
        "margin_pct": {
            "plan": None if margin_plan is None else _money(margin_plan),
            "fact": None if margin_fact is None else _money(margin_fact),
        },
        "by_month": by_month,
        "other_currency_entries_count": other_currency_count,
    }
