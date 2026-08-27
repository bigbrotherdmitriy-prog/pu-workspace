from __future__ import annotations
import re

SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")
IMPORTANT_RE = re.compile(
    r"\b(просим|требуется|необходимо|должен|обязан|срок|до\s+\d|не позднее|"
    r"стоимость|оплат|предоставить|подготовить|согласовать|вопрос)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]20\d{2}\b")
SPACED_NUMBER_RE = re.compile(r"(?<!\d)(?:\d\s+){1,}\d(?!\d)")


def normalize_document_text(text: str) -> str:
    """Repair common DOCX run boundaries such as ``2 0 2 6`` and ``2 2 %``."""
    text = SPACED_NUMBER_RE.sub(lambda match: re.sub(r"\s+", "", match.group()), text)
    text = re.sub(r"\s+([%.,;:])", r"\1", text)
    return re.sub(r"[ \t]+", " ", text)


def _first_matching(sentences: list[str], pattern: str) -> str | None:
    expression = re.compile(pattern, re.IGNORECASE)
    return next((item for item in sentences if expression.search(item)), None)


def brief_summary(text: str, source_name: str, tasks: int, drafts: int, calendar_events: int) -> str:
    text = normalize_document_text(text)
    sentences = []
    for raw in SPLIT_RE.split(text):
        sentence = " ".join(raw.split()).strip(" -–—\t")
        if 20 <= len(sentence) <= 450 and sentence not in sentences:
            sentences.append(sentence)
    document_kind = _first_matching(sentences, r"\bакт(?:а|ом|у)?\b.*\b(?:сдач|при[её]м|выполненн|работ)")
    contract = _first_matching(sentences, r"\b(?:договор|контракт)(?:а|у|ом)?\b")
    amount = _first_matching(sentences, r"\b(?:общая стоимость|стоимость работ|сумма|ндс)\b")
    parties = _first_matching(sentences, r"\b(?:заказчик|подрядчик|исполнитель)\b")
    action = _first_matching(sentences, r"\b(?:просим|требуется|необходимо|должен|обязан|предоставить|устранить|согласовать)\b")
    structured = []
    for label, value in (("Документ", document_kind), ("Основание", contract), ("Сумма", amount), ("Стороны", parties)):
        # One sentence can legitimately describe both the document and its contract basis.
        if value:
            structured.append((label, value))
    dates = []
    for value in DATE_RE.findall(text):
        if value not in dates:
            dates.append(value)
    lines = [f"📄 Краткая сводка: {source_name}"]
    if structured:
        lines.extend(f"• {label}: {value}" for label, value in structured[:4])
    else:
        ranked = sorted(enumerate(sentences), key=lambda pair: (0 if IMPORTANT_RE.search(pair[1]) else 1, pair[0]))
        lines.extend(f"• {item}" for _, item in ranked[:3])
    if dates:
        lines.append("📅 Найденные даты: " + ", ".join(dates[:8]))
    if action:
        lines.append("🎯 Требует действия: " + action)
    elif tasks == 0:
        lines.append("ℹ️ Явных поручений и сроков исполнения в тексте не обнаружено.")
    lines.append(f"✅ Задач: {tasks} · Calendar: {calendar_events} · Черновиков ответов: {drafts}")
    lines.append("Перед использованием проверьте выводы по исходному файлу.")
    return "\n".join(lines)[:4000]
