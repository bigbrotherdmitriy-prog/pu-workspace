"""Safe invoice extraction: LLM proposals only, with verbatim evidence and regex fallback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy.orm import Session

from app.ai_policy import ExternalAIBlocked, apply_ai_policy_mode, policy_mode_for_project
from app.document_extraction import CONFIDENCE_FROM_LLM_LEVEL, verify_verbatim_evidence
from app.gemini_analysis import deepseek_configured, extract_invoice_fields_with_gemini, gemini_configured


@dataclass(slots=True, frozen=True)
class InvoiceFields:
    amount: Decimal | None
    amount_evidence_quote: str | None
    currency: str
    counterparty: str | None
    counterparty_evidence_quote: str | None
    payment_purpose: str | None
    payment_purpose_evidence_quote: str | None
    suggested_category_name: str | None
    category_evidence_quote: str | None
    planned_date: date | None
    confidence: float
    extraction_method: str
    fallback_reason: str | None = None


_CATEGORY_GUARDS = {
    "прямые": ("материал", "оборудован", "работ", "монтаж", "бетон", "кабел", "постав"),
    "накладные": ("накладн", "общехозяй", "административ", "офис", "связь"),
    "зарплата": ("зарплат", "заработ", "оклад", "оплата труда", "вознагражден"),
    "аренда": ("аренд", "лизинг"),
    "командировки": ("командиров", "проезд", "гостиниц", "суточн"),
}
_CURRENCY_CODES = {"RUB", "USD", "EUR", "CNY", "KZT", "BYN"}
_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
    "ноябр": 11, "декабр": 12,
}
_MONTH_GROUP = (
    r"(?:(январ[ья]|феврал[ья]|марта?|апрел[ья]|ма[йя]|"
    r"июн[ья]|июл[ья]|августа?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])"
    r"|(янв|фев|мар|апр|июн|июл|авг|сен|сент|окт|ноя|дек)\.)"
)
_DATE_TOKEN = r"(?<!\d)([0-3]?\d)[.\-/]([01]?\d)[.\-/](20\d{2})(?!\d)"
_TEXT_DATE_TOKEN = (
    r"[«\"]?(\d{1,2})[»\"]?\s+" + _MONTH_GROUP + r"\s+(20\d{2})"
)
_DATE_RE = re.compile(_DATE_TOKEN)
_TEXT_DATE_RE = re.compile(_TEXT_DATE_TOKEN, re.IGNORECASE)
_PAYMENT_DEADLINE_RE = re.compile(
    r"(?:срок\s+(?:оплаты|платежа)|дата\s+(?:оплаты|платежа)|"
    r"(?:оплатить|оплата|плат[её]ж)\s+(?:до|не\s+позднее)|"
    r"к\s+оплате\s+(?:до|не\s+позднее))"
    r"[^\d\r\n.!?]{0,24}" + _DATE_TOKEN,
    re.IGNORECASE,
)
_PAYMENT_DEADLINE_TEXT_RE = re.compile(
    r"(?:срок\s+(?:оплаты|платежа)|дата\s+(?:оплаты|платежа)|"
    r"(?:оплатить|оплата|плат[её]ж)\s+(?:до|не\s+позднее)|"
    r"к\s+оплате\s+(?:до|не\s+позднее))"
    r"[^\d\r\n.!?]{0,24}" + _TEXT_DATE_TOKEN,
    re.IGNORECASE,
)


def _money(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 and amount <= Decimal("9999999999999999.99") else None


def _safe_text(value: object, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def _category_supported(name: str, evidence: str) -> bool:
    markers = _CATEGORY_GUARDS.get(name.casefold())
    if markers is None:  # Owner-managed categories cannot be hard-coded here.
        return True
    normalized = evidence.casefold()
    return any(marker in normalized for marker in markers)


def _date_from_match(match: re.Match[str]) -> date | None:
    groups = match.groups()[-3:]
    try:
        return date(int(groups[2]), int(groups[1]), int(groups[0]))
    except ValueError:
        return None


def _month_number(name: str) -> int | None:
    word = name.casefold().rstrip(".")
    for key, number in _MONTHS.items():
        if word.startswith(key):
            if key == "ма" and word not in ("май", "мая"):
                continue
            return number
    return None


def _text_date_from_match(match: re.Match[str]) -> date | None:
    groups = match.groups()
    day = groups[0]
    month = groups[1] or groups[2]
    year = groups[3]
    month_number = _month_number(month or "")
    if month_number is None:
        return None
    try:
        return date(int(year), month_number, int(day))
    except ValueError:
        return None


def _date_hint(text: str) -> date | None:
    best: tuple[int, date] | None = None
    for regex, builder in ((_DATE_RE, _date_from_match), (_TEXT_DATE_RE, _text_date_from_match)):
        for match in regex.finditer(text):
            parsed = builder(match)
            if parsed is not None:
                if best is None or match.start() < best[0]:
                    best = (match.start(), parsed)
                break
    return best[1] if best else None


def _planned_date_hint(text: str) -> date | None:
    """Prefer an explicitly labelled payment deadline over invoice metadata dates."""
    best: tuple[int, date] | None = None
    for regex, builder in (
        (_PAYMENT_DEADLINE_RE, _date_from_match),
        (_PAYMENT_DEADLINE_TEXT_RE, _text_date_from_match),
    ):
        for match in regex.finditer(text):
            parsed = builder(match)
            if parsed is not None:
                if best is None or match.start() < best[0]:
                    best = (match.start(), parsed)
                break
    if best is not None:
        return best[1]
    return _date_hint(text)


_SUPPLIER_INN_RE = re.compile(r"ИНН\s*:?\s*(\d{10}|\d{12})", re.IGNORECASE)
_SUPPLIER_WORDS_RE = re.compile(r"(?:Поставщик|Исполнитель|Получатель)", re.IGNORECASE)
_BUYER_NAME_RE = re.compile(
    r"(?:Покупатель|Плательщик|Заказчик)\s*:?\s*([^,\r\n]+?)(?=\s*,\s*|\s+ИНН|\r|\n|$)",
    re.IGNORECASE,
)


def supplier_inn(text: str) -> str | None:
    for word in _SUPPLIER_WORDS_RE.finditer(text):
        window = text[word.start():word.start() + 250]
        match = _SUPPLIER_INN_RE.search(window)
        if match:
            return match.group(1)
    return None


def buyer_name(text: str) -> str | None:
    match = _BUYER_NAME_RE.search(text)
    if not match:
        return None
    name = match.group(1).strip()
    # Flattened invoice text runs on into the next block: stop at the next label.
    name = re.split(r"\s+(?:Получатель|Поставщик|Плательщик|Грузополучатель|Адрес|КПП|ОГРН|Тел\.?|№|Товары|Счет|Счёт|Основание)\b", name, maxsplit=1)[0]
    name = name.strip(" ,;:")
    return name[:200] or None


def _regex_fallback(text: str, reason: str) -> InvoiceFields:
    matches = list(re.finditer(
        r"(?<!\d)(\d[\d\s]{2,}(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?)",
        text, re.IGNORECASE,
    ))
    amount = None
    quote = None
    if matches:
        parsed = [(_money(match.group(1).replace(" ", "").replace(",", ".")), match.group(0)) for match in matches]
        valid = [(value, evidence) for value, evidence in parsed if value is not None]
        if valid:
            amount, quote = max(valid, key=lambda pair: pair[0])
    return InvoiceFields(
        amount=amount, amount_evidence_quote=quote, currency="RUB",
        counterparty=None, counterparty_evidence_quote=None,
        payment_purpose=None, payment_purpose_evidence_quote=None,
        suggested_category_name=None, category_evidence_quote=None,
        planned_date=_planned_date_hint(text), confidence=0.35,
        extraction_method="regex", fallback_reason=reason,
    )


def extract_invoice_fields(
    db: Session, project_id: int, text: str, filename: str, category_names: list[str],
) -> InvoiceFields:
    """Return a review-only proposal. This function never writes financial records."""
    mode = policy_mode_for_project(db, project_id)
    try:
        ai_text = apply_ai_policy_mode(mode, text)
    except ExternalAIBlocked:
        return _regex_fallback(text, "policy_blocked")
    if not (gemini_configured() or deepseek_configured()):
        return _regex_fallback(text, "not_configured")
    try:
        payload = extract_invoice_fields_with_gemini(ai_text, filename, category_names)
    except Exception:  # Provider/HTTP details must not leak into a stored proposal.
        return _regex_fallback(text, "temporarily_unavailable")
    if not isinstance(payload, dict):
        return _regex_fallback(text, "invalid_response")

    amount_quote = verify_verbatim_evidence(payload.get("amount_evidence_quote"), text)
    amount = _money(payload.get("amount")) if amount_quote else None
    counterparty_quote = verify_verbatim_evidence(payload.get("counterparty_evidence_quote"), text)
    counterparty = _safe_text(payload.get("counterparty"), 500) if counterparty_quote else None
    if amount is None or counterparty is None:
        fallback = _regex_fallback(text, "ai_partial")
        if amount is None and fallback.amount is not None:
            amount = fallback.amount
            amount_quote = fallback.amount_evidence_quote
        if counterparty is None and fallback.counterparty is not None:
            counterparty = fallback.counterparty
            counterparty_quote = fallback.counterparty_evidence_quote
    purpose_quote = verify_verbatim_evidence(payload.get("payment_purpose_evidence_quote"), text)
    purpose = _safe_text(payload.get("payment_purpose"), 1000) if purpose_quote else None
    category_quote = verify_verbatim_evidence(payload.get("category_evidence_quote"), text)
    suggested = _safe_text(payload.get("suggested_category_name"), 200) if category_quote else None
    canonical = {name.casefold(): name for name in category_names}
    suggested = canonical.get(suggested.casefold()) if suggested else None
    if suggested and (not category_quote or not _category_supported(suggested, category_quote)):
        suggested = None
        category_quote = None
    currency = str(payload.get("currency") or "RUB").strip().upper()
    if currency not in _CURRENCY_CODES:
        currency = "RUB"
    method = "llm" if (amount_quote or counterparty_quote or purpose_quote or category_quote) else "regex"
    return InvoiceFields(
        amount=amount, amount_evidence_quote=amount_quote,
        currency=currency, counterparty=counterparty,
        counterparty_evidence_quote=counterparty_quote,
        payment_purpose=purpose, payment_purpose_evidence_quote=purpose_quote,
        suggested_category_name=suggested, category_evidence_quote=category_quote,
        planned_date=_planned_date_hint(text),
        confidence=CONFIDENCE_FROM_LLM_LEVEL.get(payload.get("confidence"), 0.45),
        extraction_method=method,
    )
