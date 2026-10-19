from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

SKIP_STATUSES = {"cancelled", "deleted", "rejected"}
MONTHS_NOM = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def _money_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _amount_words(value: Decimal) -> str:
    av = abs(value)
    if av >= Decimal("1000000"):
        millions = (av / Decimal("1000000")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        text = f"{millions}".replace(".", ",")
        return f"{text} млн ₽"
    whole = av.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    s = f"{int(whole):,}".replace(",", " ")
    return f"{s} ₽"


def _signed_amount_words(value: Decimal) -> str:
    sign = "−" if value < 0 else ""
    return sign + _amount_words(value)


def _month_label(month: str) -> str:
    year_s, month_s = month.split("-")
    idx = int(month_s) - 1
    name = MONTHS_NOM[idx] if 0 <= idx < 12 else month_s
    return f"{name} {year_s}"


def _entry_amount(row: Any) -> tuple[Decimal, str] | None:
    direction = row.direction
    actual_amount = getattr(row, "actual_amount", None) or Decimal("0")
    actual_date = getattr(row, "actual_date", None)
    planned_amount = getattr(row, "planned_amount", None) or Decimal("0")
    planned_date = getattr(row, "planned_date", None)

    if actual_date is not None and actual_amount > 0:
        return Decimal(actual_amount), actual_date.strftime("%Y-%m")
    if planned_amount == 0:
        return None
    if planned_date is None:
        return None
    return Decimal(planned_amount), planned_date.strftime("%Y-%m")


def plan_cash_gaps(rows: list[Any]) -> dict:
    inflow_by_month: dict[str, Decimal] = {}
    outflow_by_month: dict[str, Decimal] = {}
    expenses_by_month: dict[str, dict[str, Decimal]] = {}

    for row in rows:
        if getattr(row, "status", None) in SKIP_STATUSES:
            continue
        amount_info = _entry_amount(row)
        if amount_info is None:
            continue
        amount, month = amount_info
        direction = row.direction
        if direction == "inflow":
            inflow_by_month[month] = inflow_by_month.get(month, Decimal("0")) + amount
        else:
            outflow_by_month[month] = outflow_by_month.get(month, Decimal("0")) + amount
            title = getattr(row, "title", "") or ""
            bucket = expenses_by_month.setdefault(month, {})
            bucket[title] = bucket.get(title, Decimal("0")) + amount

    all_months = sorted(set(inflow_by_month) | set(outflow_by_month))
    months: list[dict] = []
    cumulative = Decimal("0")
    for month in all_months:
        inflow = inflow_by_month.get(month, Decimal("0"))
        outflow = outflow_by_month.get(month, Decimal("0"))
        net = inflow - outflow
        cumulative += net
        months.append({
            "month": month,
            "inflow": _money_str(inflow),
            "outflow": _money_str(outflow),
            "net": _money_str(net),
            "cumulative": _money_str(cumulative),
        })

    gaps: list[dict] = []
    for entry in months:
        cum = Decimal(entry["cumulative"])
        if cum >= 0:
            continue
        month = entry["month"]
        bucket = expenses_by_month.get(month, {})
        top = sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)[:3]
        gaps.append({
            "month": month,
            "cumulative": _money_str(cum),
            "inflow": entry["inflow"],
            "outflow": entry["outflow"],
            "top_expenses": [{"title": t, "amount": _money_str(a)} for t, a in top],
        })

    worst = None
    for g in gaps:
        if worst is None or Decimal(g["cumulative"]) < Decimal(worst["cumulative"]):
            worst = g

    first_inflow_month = None
    for month in all_months:
        if inflow_by_month.get(month, Decimal("0")) > 0:
            first_inflow_month = month
            break

    explanation = None
    if worst is not None:
        worst_month = worst["month"]
        worst_cum = Decimal(worst["cumulative"])
        outflow = Decimal(worst["outflow"])
        inflow = Decimal(worst["inflow"])
        parts = [
            f"Минимум — {_month_label(worst_month)}: {_signed_amount_words(worst_cum)}.",
            f"Расходы месяца {_amount_words(outflow)}, поступлений {_amount_words(inflow)}",
        ]
        if first_inflow_month is not None and first_inflow_month > worst_month:
            parts[-1] = f"{parts[-1]}; первое поступление — {_month_label(first_inflow_month)}"
        else:
            parts[-1] = f"{parts[-1]}"
        text = " ".join(parts) + "."
        top_expenses = worst.get("top_expenses") or []
        if top_expenses:
            names = ", ".join(
                f"{t['title']} {_amount_words(Decimal(t['amount']))}" for t in top_expenses
            )
            text += f" Крупнейшие расходы: {names}."
        explanation = text

    return {
        "months": months,
        "gaps": gaps,
        "worst": worst,
        "first_inflow_month": first_inflow_month,
        "explanation": explanation,
    }
