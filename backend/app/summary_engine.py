from __future__ import annotations
import re

SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")
IMPORTANT_RE = re.compile(
    r"\b(просим|требуется|необходимо|должен|обязан|срок|до\s+\d|не позднее|"
    r"стоимость|оплат|предоставить|подготовить|согласовать|вопрос)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]20\d{2}\b")


def brief_summary(text: str, source_name: str, tasks: int, drafts: int, calendar_events: int) -> str:
    sentences = []
    for raw in SPLIT_RE.split(text):
        sentence = " ".join(raw.split()).strip(" -–—\t")
        if 20 <= len(sentence) <= 450 and sentence not in sentences:
            sentences.append(sentence)
    ranked = sorted(enumerate(sentences), key=lambda pair: (0 if IMPORTANT_RE.search(pair[1]) else 1, pair[0]))
    selected = [sentence for _, sentence in ranked[:3]]
    dates = []
    for value in DATE_RE.findall(text):
        if value not in dates:
            dates.append(value)
    lines = [f"📄 Краткая сводка: {source_name}"]
    if selected:
        lines.extend(f"• {item}" for item in selected)
    else:
        lines.append("• Машиночитаемый текст найден, но ключевые утверждения не выделены.")
    if dates:
        lines.append("📅 Найденные даты: " + ", ".join(dates[:8]))
    lines.append(f"✅ Задач: {tasks} · Calendar: {calendar_events} · Черновиков ответов: {drafts}")
    lines.append("Перед использованием проверьте выводы по исходному файлу.")
    return "\n".join(lines)[:4000]
