"""Read-only views of one DDS row snapshot; no ledger, posting or forecast."""
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.mvp4.finance_guards import IMPLICIT_LEDGER_CURRENCY, finance_decision_requirements

MAX_PERIOD_DAYS = 366


def valid_period(start: date, end: date) -> bool:
    return 0 <= (end - start).days < MAX_PERIOD_DAYS


def _amount(value: object) -> Decimal | None:
    # DB Numeric delivers Decimal; do not accept float binary-money arithmetic.
    if not isinstance(value, (Decimal, int, str)) or isinstance(value, bool):
        return None
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
            return None
        return amount
    except (InvalidOperation, ValueError):
        return None


def _zero():
    return {"inflow": Decimal(0), "outflow": Decimal(0), "net": Decimal(0)}


def _money(values):
    return {key: format(value, ".2f") for key, value in values.items()}


def project_cash_flow_views(rows: Sequence[Mapping], *, project_id: int,
                            contract_id: int | None, date_from: date, date_to: date) -> dict:
    if not valid_period(date_from, date_to):
        raise ValueError("cash_flow_view_period_invalid")
    if len({row["id"] for row in rows}) != len(rows) or any(
        row["project_id"] != project_id or (contract_id is not None and row["contract_id"] != contract_id)
        for row in rows
    ):
        raise ValueError("cash_flow_view_scope_invalid")
    days, months = {}, {}
    # Integer offsets also support date.max without an overflow on the last day.
    for offset in range((date_to - date_from).days + 1):
        day = date_from + timedelta(days=offset)
        days[day.isoformat()] = {"date": day.isoformat(), "planned": _zero(), "actual": _zero(),
                                "planned_entry_ids": [], "actual_entry_ids": []}
        months.setdefault(day.isoformat()[:7], {"month": day.isoformat()[:7], "planned": _zero(), "actual": _zero()})
    totals = {"planned": _zero(), "actual": _zero()}
    excluded = dict.fromkeys(("proposed", "cancelled", "unsupported_status", "invalid_actual", "invalid_plan", "invalid_direction"), 0)
    details = []
    for source in rows:
        row = dict(source)
        status, direction = row["status"], row["direction"]
        planned_amount, actual_amount = _amount(row["planned_amount"]), _amount(row["actual_amount"])
        planned_date, actual_date = row["planned_date"], row["actual_date"]
        reasons = []
        active = status in {"approved", "paid", "received"}
        if not active:
            reasons.append(status if status in {"proposed", "cancelled"} else "unsupported_status")
        valid_direction = direction in {"inflow", "outflow"}
        if not valid_direction:
            reasons.append("invalid_direction")
        plan_valid = type(planned_date) is date and planned_amount is not None
        if active and not plan_valid:
            reasons.append("invalid_plan")
        is_fact = status in {"paid", "received"}
        fact_valid = (type(actual_date) is date and actual_amount is not None
                      and row["review_status"] == "confirmed"
                      and status == ("received" if direction == "inflow" else "paid"))
        if is_fact and not fact_valid:
            reasons.append("invalid_actual")
        plan_in = active and valid_direction and plan_valid and date_from <= planned_date <= date_to
        actual_in = is_fact and valid_direction and fact_valid and date_from <= actual_date <= date_to
        for reason in reasons:
            excluded[reason] += 1
        for kind, included, event_date, amount in (
            ("planned", plan_in, planned_date, planned_amount),
            ("actual", actual_in, actual_date, actual_amount),
        ):
            if not included:
                continue
            calendar = days[event_date.isoformat()]
            month = months[event_date.isoformat()[:7]]
            for bucket in (calendar[kind], month[kind], totals[kind]):
                bucket[direction] += amount
                bucket["net"] += amount if direction == "inflow" else -amount
            calendar[f"{kind}_entry_ids"].append(row["id"])
        row.update(planned_amount=format(planned_amount, ".2f") if planned_amount is not None else None,
                   actual_amount=format(actual_amount, ".2f") if actual_amount is not None else None,
                   planned_date=planned_date.isoformat() if type(planned_date) is date else None,
                   actual_date=actual_date.isoformat() if type(actual_date) is date else None,
                   plan_in_period=bool(plan_in), actual_in_period=bool(actual_in), exclusion_reasons=reasons)
        details.append(row)
    for group in (days.values(), months.values()):
        for bucket in group:
            bucket["planned"] = _money(bucket["planned"])
            bucket["actual"] = _money(bucket["actual"])
    return {"scope": {"project_id": project_id, "contract_id": contract_id,
                      "date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
            "currency": IMPLICIT_LEDGER_CURRENCY, "details": details,
            "calendar": list(days.values()), "months": list(months.values()),
            "summary": {kind: _money(value) for kind, value in totals.items()}, "excluded": excluded,
            "decision_requirements": finance_decision_requirements((), has_implicit_currency_rows=bool(rows), has_financial_rows=bool(rows)),
            "external_effects": {"payment_created": False, "posting_created": False, "automatic_conversion": False}}
