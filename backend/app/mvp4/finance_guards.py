"""Fail-closed finance boundaries that do not invent accounting policy."""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable


# CashFlowEntry, ProcurementItem and AcceptanceAct currently have no currency
# column and the product labels their amounts as RUB. This is a storage fact,
# not a configurable owner currency policy.
IMPLICIT_LEDGER_CURRENCY = "RUB"


def exact_decimal(value: Decimal, places: int, *, reason: str) -> Decimal:
    quantum = Decimal(1).scaleb(-places)
    if not value.is_finite() or value != value.quantize(quantum):
        raise ValueError(reason)
    return value


def currency_blockers(currency: str) -> tuple[str, ...]:
    if currency == IMPLICIT_LEDGER_CURRENCY:
        return ()
    return ("unknown_currency", "currency_conversion_policy", "exchange_rate_source")


def finance_decision_requirements(
    currencies: Iterable[str], *, has_implicit_currency_rows: bool, has_financial_rows: bool,
) -> list[dict[str, str]]:
    values = {value for value in currencies if value}
    if has_implicit_currency_rows:
        values.add(IMPLICIT_LEDGER_CURRENCY)
    codes: list[tuple[str, str, str]] = []
    if any(value != IMPLICIT_LEDGER_CURRENCY for value in values):
        codes.extend([
            ("unknown_currency", "OWNER", "Утвердить перечень рабочих валют проекта."),
            ("currency_conversion_policy", "OWNER", "Утвердить правило пересчёта без выбора курса системой."),
            ("exchange_rate_source", "OWNER", "Утвердить источник и дату валютного курса."),
        ])
    if len(values) > 1:
        codes.append(("mixed_currency", "OWNER", "Разделить итоги по валютам или утвердить пересчёт."))
    if has_financial_rows:
        codes.extend([
            ("vat_treatment", "LEGAL", "Утвердить трактовку НДС и источник подтверждения."),
            ("retention_treatment", "LEGAL", "Утвердить применение удержаний к плану, факту и оплате."),
        ])
    return [{"code": code, "decision_by": owner, "message": message} for code, owner, message in codes]


def blocking_currency_detail(currency: str, *, linked_currency: str | None = None) -> dict:
    codes = list(currency_blockers(currency))
    if linked_currency is not None and linked_currency != currency:
        codes.insert(0, "mixed_currency")
    return {
        "code": "decision_required",
        "decision_codes": list(dict.fromkeys(codes)),
        "automatic_conversion": False,
    }
