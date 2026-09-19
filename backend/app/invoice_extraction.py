"""Safe invoice extraction: LLM proposals only, with verbatim evidence and regex fallback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy.orm import Session

from app.ai_policy import ExternalAIBlocked, apply_ai_policy_mode, policy_mode_for_project
from app.document_extraction import CONFIDENCE_FROM_LLM_LEVEL, verify_verbatim_evidence
from app.gemini_analysis import extract_invoice_fields_with_gemini, gemini_configured


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


def _date_hint(text: str) -> date | None:
    match = re.search(r"(?<!\d)([0-3]?\d)[.\-/]([01]?\d)[.\-/](20\d{2})(?!\d)", text)
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


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
        planned_date=_date_hint(text), confidence=0.35,
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
    if not gemini_configured():
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
    return InvoiceFields(
        amount=amount, amount_evidence_quote=amount_quote,
        currency=currency, counterparty=counterparty,
        counterparty_evidence_quote=counterparty_quote,
        payment_purpose=purpose, payment_purpose_evidence_quote=purpose_quote,
        suggested_category_name=suggested, category_evidence_quote=category_quote,
        planned_date=_date_hint(text),
        confidence=CONFIDENCE_FROM_LLM_LEVEL.get(payload.get("confidence"), 0.45),
        extraction_method="llm",
    )
