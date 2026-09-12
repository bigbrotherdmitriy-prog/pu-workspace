from app.gemini_analysis import (
    ANALYSIS_SCHEMA,
    COMBINED_EXTRACTION_SCHEMA,
    COMBINED_EXTRACTION_SYSTEM_INSTRUCTION,
    _generation_config,
    format_gemini_analysis,
    format_message_replies,
)


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
