from __future__ import annotations

import json
import os
from typing import Any

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "executive_summary": {"type": "string"},
        "parties": {"type": "array", "items": {"type": "string"}},
        "contract_references": {"type": "array", "items": {"type": "string"}},
        "amounts": {"type": "array", "items": {"type": "string"}},
        "dates": {"type": "array", "items": {"type": "string"}},
        "obligations": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "inconsistencies": {"type": "array", "items": {"type": "string"}},
        "missing_data": {"type": "array", "items": {"type": "string"}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "draft_reply": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": [
        "document_type", "executive_summary", "parties", "contract_references",
        "amounts", "dates", "obligations", "risks", "inconsistencies",
        "missing_data", "recommended_actions", "draft_reply", "confidence",
    ],
}


SYSTEM_INSTRUCTION = """Ты — аналитик проектной, договорной и деловой документации.
Анализируй только предоставленный текст. Не додумывай факты и не используй внешние сведения.
Каждый вывод в массивах формулируй как: вывод — основание: «короткая точная цитата».
Если сведений нет, возвращай пустой массив или прямо указывай, что данных недостаточно.
Различай факты документа, обязательства, риски, противоречия и рекомендации.
Не считай обычное описание работ поручением без явного требования или срока.
Ответ должен быть на русском языке и строго соответствовать JSON-схеме."""


def gemini_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def analyze_document_with_gemini(text: str, filename: str) -> dict[str, Any]:
    import httpx

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    base_url = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    prompt = (
        f"Имя файла: {filename}\n\n"
        "Проведи содержательный анализ документа. Особое внимание удели связи с договором, "
        "сторонам, суммам и НДС, датам, обязательствам, рискам, противоречиям, отсутствующим "
        "данным и следующим действиям.\n\nТЕКСТ ДОКУМЕНТА:\n" + text[:50_000]
    )
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": ANALYSIS_SCHEMA,
        },
    }
    with httpx.Client(timeout=90.0) as client:
        response = client.post(
            f"{base_url}/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
    parts = response.json()["candidates"][0]["content"]["parts"]
    raw = "".join(part.get("text", "") for part in parts)
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Gemini returned an unexpected response")
    return result


def format_gemini_analysis(result: dict[str, Any], filename: str) -> str:
    lines = [f"🧠 Анализ Gemini: {filename}"]
    if result.get("document_type"):
        lines.append(f"📄 Тип: {result['document_type']}")
    if result.get("executive_summary"):
        lines.append(f"\nСуть: {result['executive_summary']}")
    sections = (
        ("👥 Стороны", "parties"),
        ("🔗 Договор/основание", "contract_references"),
        ("💰 Суммы и НДС", "amounts"),
        ("📅 Даты", "dates"),
        ("✅ Обязательства", "obligations"),
        ("⚠️ Риски", "risks"),
        ("❗ Противоречия", "inconsistencies"),
        ("❓ Не хватает данных", "missing_data"),
        ("➡️ Что делать", "recommended_actions"),
    )
    for title, key in sections:
        values = result.get(key) or []
        if values:
            lines.append("\n" + title + ":")
            lines.extend(f"• {str(value)[:600]}" for value in values[:5])
    draft = str(result.get("draft_reply") or "").strip()
    if draft:
        lines.append("\n✉️ Проект ответа:\n" + draft[:900])
    confidence = {"high": "высокая", "medium": "средняя", "low": "низкая"}.get(result.get("confidence"), "не указана")
    lines.append(f"\nУверенность анализа: {confidence}. Проверьте выводы по исходному документу.")
    return "\n".join(lines)[:4000]
