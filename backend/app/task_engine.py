from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.management import Obligation
from app.models.user import User
from app.core.integration_types import StorageObject

OBLIGATION_RE = re.compile(
    r"\b(должен|должна|должны|обязан|обязана|обязаны|необходимо|следует|"
    r"поручить|поручено|предоставить|подготовить|согласовать|направить|"
    r"выполнить|устранить|оплатить|поставить)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b(?:до\s+|не позднее\s+)?(\d{1,2})[./](\d{1,2})[./](20\d{2})\b", re.IGNORECASE)
MONTH_DATE_RE = re.compile(
    r"\b(?:до\s+|не позднее\s+)?(\d{1,2})\s+"
    r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"(?:\s+(20\d{2}))?\b", re.IGNORECASE,
)
MONTHS = {name: index + 1 for index, name in enumerate(
    ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"))}
SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")


@dataclass(slots=True)
class TaskCandidate:
    """Candidate score is a heuristic review signal, not a calibrated probability."""

    title: str
    excerpt: str
    due_date: date | None
    priority: str
    confidence: float
    review_reasons: tuple[str, ...] = ()


def _text_quality_review_reasons(text: str) -> tuple[str, ...]:
    """Flag explicit corruption only; do not spell-correct, classify jargon or drop claims.

    This is NOT OCR confidence. In particular, numbers, uppercase identifiers,
    separate Latin words and single mixed-script tokens are not evidence of damage.
    """
    reasons: list[str] = []
    if "\ufffd" in text:
        reasons.append("В тексте есть символы замены: часть исходных знаков не распознана.")
    if any(unicodedata.category(char) in {"Cc", "Cs", "Co"} and not char.isspace() for char in text):
        reasons.append("В тексте есть служебные или нестандартные символы вместо читаемых знаков.")
    # Repeated UTF-8-as-cp1251/latin1 byte pairs, not any occurrence of Р/С.
    if re.search(r"(?:[РС][\u0080-\u052f\u2000-\u2122]){3,}|(?:[ÃÂÐÑ][\u0080-\u00bf]){3,}", text):
        reasons.append("Есть характерная последовательность повреждённой кодировки.")
    if re.search(r"\b[а-яё](?:\s+[а-яё]){5,}\b", text):
        reasons.append("Есть длинная последовательность текста, разбитого на отдельные буквы.")
    if re.search(r"([а-яёa-z])\1{5,}", text):
        reasons.append("Есть необычный повтор одной буквы; сверьте фрагмент с документом.")
    if re.search(r"[?#~|^*]{4,}", text):
        reasons.append("Есть скопление нечитаемых символов; смысл фрагмента требует проверки.")
    # Require two word-like fragments with *interior* substitutions. Do not penalize
    # M8x20, 12Х18Н10Т, AB12-РС34, ИД, API, 1С or separate language segments.
    substitutions = sum(
        1 for token in re.findall(r"[^\W_]+", text)
        if len(token) >= 5
        and re.fullmatch(r"[а-яё]+[aceopxy0-9][а-яё]+", token)
        and len(re.findall(r"[а-яё]", token)) >= 3
    )
    if substitutions >= 2:
        reasons.append("В нескольких словах есть вероятные подмены букв цифрами или латиницей.")
    return tuple(reasons)


def extract_task_candidates(text: str | None, limit: int = 5) -> list[TaskCandidate]:
    if not text:
        return []
    result: list[TaskCandidate] = []
    seen: set[str] = set()
    for raw in SENTENCE_RE.split(text):
        sentence = " ".join(raw.split()).strip(" -–—\t")
        if len(sentence) < 18 or len(sentence) > 1200 or not OBLIGATION_RE.search(sentence):
            continue
        digest = hashlib.sha256(sentence.casefold().encode()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        match = DATE_RE.search(sentence)
        due = None
        if match:
            try:
                due = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            except ValueError:
                pass
        if due is None:
            word_match = MONTH_DATE_RE.search(sentence)
            if word_match:
                try:
                    due = date(int(word_match.group(3) or date.today().year), MONTHS[word_match.group(2).lower()], int(word_match.group(1)))
                except ValueError:
                    pass
        urgent = bool(re.search(r"\b(срочно|критич|немедленно|не позднее)\b", sentence, re.I))
        review_reasons = _text_quality_review_reasons(sentence)
        # Compatibility scores for clean candidates; a valid date cannot override
        # damaged evidence. 0.45 is a conservative review ceiling, not 45% accuracy.
        confidence = 0.90 if due else 0.82
        if review_reasons:
            confidence = min(confidence, 0.45)
        result.append(TaskCandidate(sentence[:240], sentence, due, "high" if urgent else "normal", confidence, review_reasons))
        if len(result) >= limit:
            break
    return result


def _default_assignee(db: Session, project_id: int) -> User | None:
    role_order = {"owner": 0, "manager": 1, "editor": 2, "member": 3, "viewer": 4}
    rows = db.execute(
        select(User, ProjectMember.role)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
    ).all()
    if not rows:
        return db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))
    rows.sort(key=lambda row: (role_order.get(row.role, 9), row.User.id))
    return rows[0].User


def create_tasks_from_files(db: Session, project_id: int, session_id: int | None, files: list[StorageObject], source_type: str = "document_analysis") -> list[Task]:
    assignee = _default_assignee(db, project_id)
    if not assignee:
        return []
    created: list[Task] = []
    for file in files:
        if file.is_folder:
            continue
        for candidate in extract_task_candidates(file.content_text):
            excerpt_hash = hashlib.sha256(candidate.excerpt.casefold().encode()).hexdigest()
            existing = db.scalar(select(Task.id).where(
                Task.project_id == project_id,
                Task.source_file_id == file.id,
                Task.source_excerpt_hash == excerpt_hash,
            ))
            if existing:
                continue
            task = Task(
                project_id=project_id,
                assignee_user_id=assignee.id,
                created_by_user_id=assignee.id,
                organizer_session_id=session_id,
                title=candidate.title,
                description=(
                    f"Автоматически выделено из документа «{file.name}». "
                    "Оценка эвристическая, не вероятность правильного распознавания. "
                    "Требуется ручная проверка по исходной цитате."
                    + (" Причины: " + " ".join(candidate.review_reasons) if candidate.review_reasons else "")
                ),
                status="assigned",
                priority=candidate.priority,
                due_date=candidate.due_date,
                source_file_id=file.id,
                source_file_name=file.name,
                source_excerpt=candidate.excerpt,
                source_excerpt_hash=excerpt_hash,
                confidence=candidate.confidence,
                needs_review=True,
                source_type=source_type,
            )
            db.add(task)
            db.flush()
            db.add(Obligation(
                project_id=project_id, owner_user_id=assignee.id, task_id=task.id,
                title=candidate.title, due_date=candidate.due_date,
                source_type=source_type, source_id=file.id, source_name=file.name,
                source_excerpt=candidate.excerpt, source_hash=excerpt_hash,
                confidence=candidate.confidence,
            ))
            created.append(task)
    db.commit()
    for task in created:
        db.refresh(task)
    return created
