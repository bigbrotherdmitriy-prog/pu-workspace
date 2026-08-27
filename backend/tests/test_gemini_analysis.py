from app.gemini_analysis import format_gemini_analysis


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
