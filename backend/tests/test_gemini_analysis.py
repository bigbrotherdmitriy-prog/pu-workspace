import json

import httpx
import pytest

from app.gemini_analysis import (
    ANALYSIS_SCHEMA,
    COMBINED_EXTRACTION_SCHEMA,
    COMBINED_EXTRACTION_SYSTEM_INSTRUCTION,
    _generation_config,
    extract_combined_fields_with_gemini,
    format_gemini_analysis,
    format_message_replies,
)


def _iter_schema_nodes(node):
    """Recurse through a JSON-Schema-shaped dict, yielding every node."""
    if not isinstance(node, dict):
        return
    yield node
    for child in (node.get("properties") or {}).values():
        yield from _iter_schema_nodes(child)
    if "items" in node:
        yield from _iter_schema_nodes(node["items"])


def test_gemini_3_generation_config_uses_low_thinking_without_sampling_overrides():
    config = _generation_config("gemini-3.8-flash", ANALYSIS_SCHEMA, 0.1)

    assert config["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "temperature" not in config
    assert "topP" not in config
    assert "topK" not in config
    assert config["responseSchema"] is ANALYSIS_SCHEMA


def test_legacy_gemini_generation_config_keeps_supported_temperature():
    config = _generation_config("gemini-2.5-flash", ANALYSIS_SCHEMA, 0.1)

    assert config["temperature"] == 0.1
    assert "thinkingConfig" not in config


def test_gemini_analysis_is_formatted_as_actionable_sections():
    result = {
        "document_type": "Акт сдачи-приёмки",
        "executive_summary": "Подтверждает выполнение работ по контракту.",
        "parties": ["Заказчик — основание: «ООО Заказчик»"],
        "contract_references": ["Контракт №1 — основание: «по контракту №1»"],
        "amounts": ["100 рублей — основание: «стоимость 100 рублей»"],
        "dates": [], "obligations": [],
        "risks": ["Не заполнена дата — основание: «___ 2026 г.»"],
        "inconsistencies": [], "missing_data": [],
        "recommended_actions": ["Проверить дату подписания"],
        "draft_reply": "Просим заполнить дату.",
        "confidence": "high",
    }
    message = format_gemini_analysis(result, "Акт.docx")
    assert "Анализ Gemini" in message
    assert "⚠️ Риски" in message
    assert "Что делать" in message
    assert "Проект ответа" in message
    assert "высокая" in message


def test_message_replies_are_formatted_as_three_safe_drafts():
    message = format_message_replies({
        "message_summary": "Просят уточнить фамилии и приложить страницы.",
        "requires_reply": True,
        "short_reply": "Уточню данные и направлю страницы.",
        "business_reply": "Добрый день. Уточним данные и направим необходимые страницы.",
        "casual_reply": "Хорошо, уточню и пришлю нужные страницы.",
        "recommended_action": "Проверить фамилии перед отправкой.",
        "confidence": "high",
    })
    assert "Краткий" in message
    assert "Деловой" in message
    assert "Обычный" in message
    assert "не отправлены автоматически" in message


def test_combined_extraction_schema_covers_all_four_categories_per_file():
    top = COMBINED_EXTRACTION_SCHEMA["properties"]
    assert set(top) == {"obligations", "response_candidates", "risks", "decisions"}
    assert set(COMBINED_EXTRACTION_SCHEMA["required"]) == set(top)

    obligation_props = top["obligations"]["items"]["properties"]
    for field in (
        "title", "evidence_quote", "due_date", "due_date_evidence_quote",
        "assignee_hint", "assignee_evidence_quote",
        "amount", "amount_currency", "amount_evidence_quote", "confidence",
    ):
        assert field in obligation_props

    risk_props = top["risks"]["items"]["properties"]
    assert risk_props["kind"]["enum"] == ["risk", "deviation"]
    assert risk_props["criticality"]["enum"] == ["medium", "high"]


def test_combined_extraction_generation_config_uses_the_combined_schema():
    config = _generation_config("gemini-3.6-flash", COMBINED_EXTRACTION_SCHEMA, 0.1)
    assert config["responseSchema"] is COMBINED_EXTRACTION_SCHEMA
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}


def test_combined_extraction_instruction_requires_verbatim_evidence():
    assert "ДОСЛОВНОЙ подстрокой" in COMBINED_EXTRACTION_SYSTEM_INSTRUCTION
    assert "не выдумывай значение" in COMBINED_EXTRACTION_SYSTEM_INSTRUCTION
    # Extends, rather than replaces, the shared base instruction.
    assert "Не считай обычное описание работ поручением" in COMBINED_EXTRACTION_SYSTEM_INSTRUCTION


def test_combined_extraction_schema_never_uses_type_as_a_list():
    """Pins the real Gemini API constraint found via a live smoke test against
    production (docs/audits/mvp2-llm-extraction-implementation.md §7):
    responseSchema is protobuf-backed and rejects "type" as a JSON-Schema-
    style union list (e.g. ["string", "null"]) with
    400 INVALID_ARGUMENT / "Proto field is not repeating, cannot start
    list" -- even though ai.google.dev's own structured-output guide shows
    that form. Every node in every schema sent to Gemini must have "type"
    as a single string. This is a static, no-network guard: it would have
    caught the original bug without needing GEMINI_API_KEY.
    """
    for schema in (ANALYSIS_SCHEMA, COMBINED_EXTRACTION_SCHEMA):
        for node in _iter_schema_nodes(schema):
            assert not isinstance(node.get("type"), list), f"type must not be a list: {node}"


def test_nullable_obligation_fields_use_type_plus_nullable_not_a_type_list():
    """The confirmed-working syntax (single "type" + "nullable": true), per
    a working example in https://github.com/google-gemini/generative-ai-js/
    issues/188 -- the Vertex AI Schema message this compiles to has no
    union-type concept, only a "nullable" boolean sibling to "type"."""
    obligation_props = COMBINED_EXTRACTION_SCHEMA["properties"]["obligations"]["items"]["properties"]
    nullable_fields = (
        "due_date", "due_date_evidence_quote", "assignee_hint",
        "assignee_evidence_quote", "amount", "amount_currency", "amount_evidence_quote",
    )
    for field in nullable_fields:
        prop = obligation_props[field]
        assert isinstance(prop["type"], str), f"{field}: type must be a single string"
        assert prop.get("nullable") is True, f"{field}: must declare nullable=True"
    # title/evidence_quote/confidence are never null -- must not carry the flag.
    for field in ("title", "evidence_quote", "confidence"):
        assert "nullable" not in obligation_props[field]


def test_extract_combined_fields_propagates_the_exact_production_schema_error(monkeypatch):
    """Reproduces the literal HTTP 400 body Gemini returned in production
    before this fix (docs/audits/mvp2-llm-extraction-implementation.md §7),
    to confirm extract_combined_fields_with_gemini surfaces such an error
    rather than swallowing it -- document_extraction.py's fallback (Вариант
    А) depends on this exception actually propagating.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    request = httpx.Request(
        "POST", "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
    )
    body = json.dumps({
        "error": {
            "code": 400,
            "message": (
                "Invalid JSON payload received. Unknown name \"type\" at "
                "'generation_config.response_schema.properties[0].value.items.properties[2].value': "
                "Proto field is not repeating, cannot start list."
            ),
            "status": "INVALID_ARGUMENT",
        },
    })
    response = httpx.Response(400, request=request, content=body)

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        raise httpx.HTTPStatusError("400 Bad Request", request=request, response=response)

    monkeypatch.setattr("app.gemini_analysis.request_with_retry", fake_request_with_retry)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        extract_combined_fields_with_gemini("Синтетический текст.", "f.txt", "Проект", [])
    assert exc_info.value.response.status_code == 400
    assert "Proto field is not repeating" in exc_info.value.response.text
