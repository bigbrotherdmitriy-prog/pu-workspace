from __future__ import annotations

import json
import os
from typing import Any

from app.core.external_retry import HEAVY_AI_RETRY, request_with_retry

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

MESSAGE_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "message_summary": {"type": "string"},
        "requires_reply": {"type": "boolean"},
        "short_reply": {"type": "string"},
        "business_reply": {"type": "string"},
        "casual_reply": {"type": "string"},
        "recommended_action": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": [
        "message_summary", "requires_reply", "short_reply", "business_reply",
        "casual_reply", "recommended_action", "confidence",
    ],
}

_OBLIGATION_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "evidence_quote": {"type": "string"},
        # "type" as a list (JSON-Schema-style ["string", "null"]) is rejected
        # by the real Gemini responseSchema ("Proto field is not repeating,
        # cannot start list" -- confirmed via a live smoke test against the
        # production key, docs/audits/mvp2-llm-extraction-implementation.md
        # §7). Despite ai.google.dev/gemini-api/docs/structured-output
        # showing the list form, the actual API only accepts a single
        # "type" plus a separate "nullable" boolean -- confirmed against a
        # working example in https://github.com/google-gemini/
        # generative-ai-js/issues/188 and the parallel Vertex AI Schema
        # message, which is what responseSchema actually compiles to.
        "due_date": {"type": "string", "nullable": True},
        "due_date_evidence_quote": {"type": "string", "nullable": True},
        "assignee_hint": {"type": "string", "nullable": True},
        "assignee_evidence_quote": {"type": "string", "nullable": True},
        "amount": {"type": "number", "nullable": True},
        "amount_currency": {"type": "string", "nullable": True},
        "amount_evidence_quote": {"type": "string", "nullable": True},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": [
        "title", "evidence_quote", "due_date", "due_date_evidence_quote",
        "assignee_hint", "assignee_evidence_quote",
        "amount", "amount_currency", "amount_evidence_quote", "confidence",
    ],
}

# One combined call per file replaces the LLM-relevant part of all three
# regex engines (task_engine/response_engine/governance_engine) at once,
# not just the four obligation sub-fields -- see docs/audits/
# mvp2-ai-extraction-preflight.md §9-10 for why (RPM/cost of 3 calls/file
# vs 1 across a 2000-file organizer scan).
COMBINED_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "obligations": {"type": "array", "items": _OBLIGATION_ITEM_SCHEMA},
        "response_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "evidence_quote": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["evidence_quote", "confidence"],
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "kind": {"type": "string", "enum": ["risk", "deviation"]},
                    "criticality": {"type": "string", "enum": ["medium", "high"]},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["title", "evidence_quote", "kind", "criticality", "confidence"],
            },
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["question", "evidence_quote", "confidence"],
            },
        },
    },
    "required": ["obligations", "response_candidates", "risks", "decisions"],
}


MAIL_COMPOSER_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["subject", "body", "notes"],
}


SYSTEM_INSTRUCTION = """Ты — аналитик проектной, договорной и деловой документации.
Анализируй только предоставленный текст. Не додумывай факты и не используй внешние сведения.
Каждый вывод в массивах формулируй как: вывод — основание: «короткая точная цитата».
Если сведений нет, возвращай пустой массив или прямо указывай, что данных недостаточно.
Различай факты документа, обязательства, риски, противоречия и рекомендации.
Не считай обычное описание работ поручением без явного требования или срока.
Ответ должен быть на русском языке и строго соответствовать JSON-схеме."""


COMBINED_EXTRACTION_SYSTEM_INSTRUCTION = SYSTEM_INSTRUCTION + """

Дополнительно для этой задачи:
Каждое поле "*_evidence_quote" должно быть ДОСЛОВНОЙ подстрокой исходного
текста, без перефразирования и без сокращений многоточием — иначе цитату
нельзя будет найти в документе. Если основания для поля нет, верни null
в самом поле и null в его evidence_quote, не выдумывай значение.
due_date возвращай в формате ISO 8601 (YYYY-MM-DD), только если в тексте
есть явный маркер срока ("не позднее", "до", "к", "срок:") — обычная дата
в тексте (например, дата документа или ссылка на нормативный акт) не
является сроком исполнения.
assignee_hint — это то, как ответственный назван в тексте (имя, фамилия
или должность), а не идентификатор пользователя. Если в промпте передан
список участников проекта — предпочитай указывать одного из них, если
текст на это указывает; не изобретай имя, которого нет ни в тексте, ни
в списке участников.
amount — число без разделителей тысяч и без указания валюты словами;
валюту (например, "RUB", "USD") возвращай отдельно в amount_currency."""


def gemini_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def _generation_config(model: str, schema: dict[str, Any], legacy_temperature: float) -> dict[str, Any]:
    """Build a model-compatible structured-output configuration.

    Gemini 3.x rejects/deprecates sampling overrides and should use the new
    thinking-level control.  LOW is intentional for interactive document and
    message analysis: it bounds latency while the JSON schema and system
    instruction provide the required determinism.
    """
    config: dict[str, Any] = {
        "responseMimeType": "application/json",
        "responseSchema": schema,
    }
    if model.casefold().startswith("gemini-3"):
        config["thinkingConfig"] = {"thinkingLevel": "low"}
    else:
        config["temperature"] = legacy_temperature
    return config


def analyze_document_with_gemini(text: str, filename: str) -> dict[str, Any]:
    import httpx

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
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
        "generationConfig": _generation_config(model, ANALYSIS_SCHEMA, 0.1),
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


def extract_combined_fields_with_gemini(
    text: str, filename: str, project_name: str, member_names: list[str],
) -> dict[str, Any]:
    """One call replacing the LLM-relevant work of task/response/governance engines.

    Raises RuntimeError if no API key is configured, or the underlying
    httpx/JSON error otherwise -- callers classify and fall back (see
    app/document_extraction.py), this function does not swallow failures.
    """
    import httpx

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    base_url = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    members_line = ", ".join(member_names) if member_names else "(список участников недоступен)"
    prompt = (
        f"Имя файла: {filename}\n"
        f"Проект: {project_name}\n"
        f"Участники проекта: {members_line}\n\n"
        "Извлеки из текста: поручения (с их сроком, ответственным и суммой, если есть), "
        "формулировки, требующие ответа, риски/отклонения и вопросы, требующие решения. "
        "Обычное описание уже выполненных работ или технических характеристик не является "
        "поручением.\n\nТЕКСТ ДОКУМЕНТА:\n" + text[:50_000]
    )
    payload = {
        "systemInstruction": {"parts": [{"text": COMBINED_EXTRACTION_SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": _generation_config(model, COMBINED_EXTRACTION_SCHEMA, 0.1),
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


def analyze_message_with_gemini(text: str, context_name: str) -> dict[str, Any]:
    import httpx

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    base_url = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    prompt = (
        f"Контекст: {context_name}\n\n"
        "Определи суть входящего делового сообщения и требуется ли на него ответ. "
        "Подготовь три самостоятельных варианта ответа от лица получателя: краткий, "
        "деловой официальный и обычный человеческий. Не добавляй факты, даты, обещания "
        "или вложения, которых нет во входящем сообщении. Если данных не хватает, задай "
        "уточняющий вопрос в самом ответе.\n\nВХОДЯЩЕЕ СООБЩЕНИЕ:\n" + text[:20_000]
    )
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": _generation_config(model, MESSAGE_REPLY_SCHEMA, 0.2),
    }
    with httpx.Client(timeout=90.0) as client:
        response = request_with_retry(
            client, "POST", f"{base_url}/models/{model}:generateContent",
            policy=HEAVY_AI_RETRY,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    parts = response.json()["candidates"][0]["content"]["parts"]
    result = json.loads("".join(part.get("text", "") for part in parts))
    if not isinstance(result, dict):
        raise ValueError("Gemini returned an unexpected response")
    return result


def compose_message_with_gemini(text: str, context_name: str, action: str, tone: str) -> dict[str, Any]:
    import httpx

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    base_url = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    prompt = (
        f"Контекст: {context_name}\nДействие редактора: {action}\nТон: {tone}\n\n"
        "Подготовь или переработай деловое письмо на русском языке. Верни тему, готовый текст письма "
        "и краткое примечание, что пользователю нужно проверить. Не выдумывай имена, даты, суммы, "
        "обещания, вложения или выполненные действия. Сохраняй факты исходного текста. "
        "Не добавляй подпись: приложение подставит подтверждённую подпись пользователя.\n\n"
        "ИСХОДНЫЕ ДАННЫЕ:\n" + text[:20_000]
    )
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": _generation_config(model, MAIL_COMPOSER_SCHEMA, 0.2),
    }
    with httpx.Client(timeout=90.0) as client:
        response = request_with_retry(
            client, "POST", f"{base_url}/models/{model}:generateContent",
            policy=HEAVY_AI_RETRY,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    parts = response.json()["candidates"][0]["content"]["parts"]
    result = json.loads("".join(part.get("text", "") for part in parts))
    if not isinstance(result, dict) or not str(result.get("body") or "").strip():
        raise ValueError("Gemini returned an unexpected mail draft")
    return result


def format_message_replies(result: dict[str, Any]) -> str:
    lines = ["✉️ Варианты ответа"]
    summary = str(result.get("message_summary") or "").strip()
    if summary:
        lines.append("\nСуть сообщения: " + summary[:700])
    if not result.get("requires_reply", True):
        lines.append("\nℹ️ Обязательный ответ не требуется, но ниже подготовлены варианты при необходимости.")
    variants = (
        ("⚡ Краткий", "short_reply"),
        ("💼 Деловой", "business_reply"),
        ("🙂 Обычный", "casual_reply"),
    )
    for title, key in variants:
        value = str(result.get(key) or "").strip()
        if value:
            lines.append(f"\n{title}:\n{value[:900]}")
    action = str(result.get("recommended_action") or "").strip()
    if action:
        lines.append("\n➡️ Рекомендация: " + action[:500])
    lines.append("\nЧерновики не отправлены автоматически. Проверьте факты перед использованием.")
    return "\n".join(lines)[:4000]


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
