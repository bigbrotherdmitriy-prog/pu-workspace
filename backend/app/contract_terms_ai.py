from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any

from app.gemini_analysis import gemini_configured, _generation_config

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 150_000

CONTRACT_TERMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "amount": {"type": "string"},
        "amount_evidence": {"type": "string"},
        "vat_rate": {"type": "string"},
        "vat_mode": {"type": "string", "enum": ["rate", "none", ""]},
        "vat_evidence": {"type": "string"},
        "advance_amount": {"type": "string"},
        "advance_amount_evidence": {"type": "string"},
        "advance_percent": {"type": "string"},
        "advance_percent_evidence": {"type": "string"},
        "retention_percent": {"type": "string"},
        "retention_evidence": {"type": "string"},
        "signed_at": {"type": "string"},
        "signed_at_evidence": {"type": "string"},
        "performed_from": {"type": "string"},
        "performed_from_evidence": {"type": "string"},
        "performed_to": {"type": "string"},
        "performed_to_evidence": {"type": "string"},
        "warranty_until": {"type": "string"},
        "warranty_until_evidence": {"type": "string"},
    },
    "required": [
        "amount", "amount_evidence", "vat_rate", "vat_mode", "vat_evidence",
        "advance_amount", "advance_amount_evidence",
        "advance_percent", "advance_percent_evidence",
        "retention_percent", "retention_evidence",
        "signed_at", "signed_at_evidence",
        "performed_from", "performed_from_evidence",
        "performed_to", "performed_to_evidence",
        "warranty_until", "warranty_until_evidence",
    ],
}

SYSTEM_INSTRUCTION = (
    "Ты — извлекатель полей договора. Возвращай только JSON по схеме. "
    "Для каждого значения обязательно приведи evidence — точную цитату из текста "
    "документа, из которой это значение следует. Если значение неизвестно, "
    "оставь поле пустой строкой. Суммы указывай числом без пробелов и разделителей, "
    "например 677750000.00. Рубли и копейки разделяй точкой. Даты — в формате YYYY-MM-DD. "
    "vat_mode = 'rate', если НДС указан процентом; 'none', если прямо сказано "
    "'без НДС'/'НДС не облагается'; иначе пустая строка. retention_percent — "
    "процент гарантийного удержания; если в тексте есть только НДС, не возвращай удержание. "
    "Текст может содержать основной договор и приложения/спецификации после строк "
    "\"=== Приложение: …\". Если в договоре цена указана ссылкой на спецификацию или "
    "приложение — бери итоговую сумму (с НДС, если указано) и условия оплаты, аванс, "
    "НДС из приложения. Цитата должна быть дословной из того места, откуда взято значение. "
    "По умолчанию в России применяется ставка НДС 22% — если в документе ставка не "
    "указана и нет признаков освобождения (\"без НДС\", \"НДС не облагается\", \"не является "
    "плательщиком НДС\", \"упрощ\", \"УСН\", \"346.11\", \"145 НК\", \"самозанят\", \"налог на "
    "профессиональный доход\", \"НПД\"), верни vat_mode = 'rate', vat_rate = '22'."
)


_NUMERIC_FIELDS = {
    "amount", "vat_rate", "advance_amount", "advance_percent", "retention_percent",
}
_DATE_FIELDS = {"signed_at", "performed_from", "performed_to", "warranty_until"}
_TEXT_FIELDS = {"vat_mode"}


def _normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _coerce_decimal(raw: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d,.\-]", "", (raw or "").strip())
    if not cleaned:
        return None
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _coerce_date(raw: str) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", value)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None
    return None


def extract_contract_terms_ai(text: str) -> dict[str, Any]:
    """Extract contract terms via the configured Gemini model with evidence.

    Every returned field is validated against the document: the evidence quote
    must appear verbatim (after whitespace normalization) in the source text,
    otherwise the field is discarded. Errors from the model or network are
    logged and yield an empty result -- callers must not fail the whole
    analysis because AI enrichment is unavailable.
    """
    empty: dict[str, Any] = {
        "amount": None, "advance_amount": None, "advance_percent": None,
        "retention_percent": None,
        "amount_evidence": None, "advance_evidence": None, "retention_evidence": None,
        "signed_at": None, "signed_at_evidence": None,
        "vat_mode": None, "vat_rate": None, "vat_evidence": None,
        "performed_from": None, "performed_to": None, "performed_evidence": None,
        "warranty_until": None, "warranty_evidence": None,
    }
    if not text or not text.strip():
        return empty
    if not gemini_configured():
        return empty

    try:
        payload = _call_model(text[:MAX_INPUT_CHARS])
    except Exception:
        logger.exception("contract_terms_ai: model call failed")
        return empty
    if not isinstance(payload, dict):
        return empty

    haystack = _normalize_evidence(text)

    def _evidence_ok(field: str) -> bool:
        evidence = payload.get(f"{field}_evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            return False
        return _normalize_evidence(evidence) in haystack

    result = dict(empty)

    def _apply_numeric(name: str, dtype: type[Decimal] | type[date]) -> None:
        if not _evidence_ok(name):
            return
        raw = payload.get(name)
        if not isinstance(raw, str):
            return
        value = _coerce_decimal(raw) if dtype is Decimal else _coerce_date(raw)
        if value is None:
            return
        result[name] = value
        result[f"{name}_evidence"] = payload.get(f"{name}_evidence")[:500]

    _apply_numeric("amount", Decimal)
    _apply_numeric("advance_amount", Decimal)
    _apply_numeric("advance_percent", Decimal)
    _apply_numeric("retention_percent", Decimal)
    _apply_numeric("signed_at", date)
    _apply_numeric("performed_from", date)
    _apply_numeric("performed_to", date)
    _apply_numeric("warranty_until", date)

    vat_mode = payload.get("vat_mode")
    if vat_mode in ("rate", "none") and _evidence_ok("vat"):
        result["vat_mode"] = vat_mode
        raw_rate = payload.get("vat_rate")
        if vat_mode == "rate":
            rate = _coerce_decimal(raw_rate) if isinstance(raw_rate, str) else None
            if rate is not None:
                result["vat_rate"] = rate
            else:
                result["vat_mode"] = None
        result["vat_evidence"] = payload.get("vat_evidence")[:500]

    if result["advance_amount"] is None and result["advance_percent"] is not None and result["amount"] is not None:
        result["advance_amount"] = (result["amount"] * result["advance_percent"] / Decimal("100")).quantize(Decimal("0.01"))

    return result


def _call_model(text: str) -> dict[str, Any]:
    import httpx
    import json
    import os

    from app.core.external_retry import HEAVY_AI_RETRY, request_with_retry

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    base_url = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    prompt = (
        "Извлеки поля договора из текста. Для каждого значения приведи дословную цитату. "
        "Текст может содержать основной договор и приложения/спецификации после строк "
        "\"=== Приложение: …\". Если в договоре цена указана ссылкой на спецификацию или "
        "приложение — бери итоговую сумму (с НДС, если указано) и условия оплаты, аванс, "
        "НДС из приложения. Цитата должна быть дословной из того места, откуда взято значение.\n\n"
        "ТЕКСТ ДОГОВОРА:\n" + text
    )
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": _generation_config(model, CONTRACT_TERMS_SCHEMA, 0.1),
    }
    with httpx.Client(timeout=90.0) as client:
        response = request_with_retry(
            client, "POST", f"{base_url}/models/{model}:generateContent",
            policy=HEAVY_AI_RETRY,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    parts = response.json()["candidates"][0]["content"]["parts"]
    raw = "".join(part.get("text", "") for part in parts)
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Gemini returned an unexpected response")
    return result
