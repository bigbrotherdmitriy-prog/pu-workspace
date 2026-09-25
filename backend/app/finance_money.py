from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.project import Project


MONEY_QUANTUM = Decimal("0.01")
MONEY_MAX = Decimal("9999999999999999.99")
ISO_4217_CURRENCIES = frozenset({
    "AED", "AMD", "AUD", "AZN", "BGN", "BRL", "BYN", "CAD", "CHF", "CNY",
    "CZK", "DKK", "EUR", "GBP", "GEL", "HKD", "HUF", "INR", "JPY", "KGS",
    "KRW", "KZT", "MDL", "NOK", "PLN", "RON", "RSD", "RUB", "SEK", "SGD",
    "THB", "TJS", "TRY", "UAH", "USD", "UZS", "VND", "ZAR",
})


class ProjectCurrencyError(ValueError):
    pass


def strict_currency(value: object) -> str:
    if not isinstance(value, str) or value not in ISO_4217_CURRENCIES:
        raise ValueError("Валюта должна быть поддерживаемым кодом ISO 4217 в верхнем регистре")
    return value


def money(value: object, *, allow_zero: bool = True) -> Decimal:
    if isinstance(value, float):
        raise ValueError("Денежная сумма должна передаваться десятичной строкой, а не float")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Некорректная денежная сумма") from exc
    if not amount.is_finite():
        raise ValueError("Денежная сумма должна быть конечной")
    amount = amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if amount < 0 or (not allow_zero and amount == 0):
        raise ValueError(
            "Денежная сумма должна быть положительной"
            if not allow_zero else "Денежная сумма не может быть отрицательной"
        )
    if amount > MONEY_MAX:
        raise ValueError("Денежная сумма превышает Numeric(18,2)")
    return amount


def to_minor_units(value: object) -> int:
    return int(money(value) * 100)


def from_minor_units(value: int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Копейки должны быть целым числом")
    return (Decimal(value) / 100).quantize(MONEY_QUANTUM)


def money_string(value: object) -> str:
    return format(money(value), ".2f")


def project_currency(db: Session, project_id: int) -> str:
    project = db.get(Project, project_id)
    if project is None:
        raise ProjectCurrencyError("Проект не найден")
    return strict_currency(project.currency)


def require_project_currency(db: Session, project_id: int, currency: object) -> str:
    requested = strict_currency(currency)
    expected = project_currency(db, project_id)
    if requested != expected:
        raise ProjectCurrencyError(
            f"Валюта {requested} не совпадает с валютой проекта {expected}"
        )
    return expected
