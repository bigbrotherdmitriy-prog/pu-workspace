from app.gemini_analysis import ANALYSIS_SCHEMA, _generation_config, format_gemini_analysis, format_message_replies


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
