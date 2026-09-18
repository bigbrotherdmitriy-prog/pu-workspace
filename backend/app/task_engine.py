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
# Keep the prefilter and the regex fallback on the same deliberately narrow
# imperative vocabulary. Match complete forms, not stems such as "готов" that
# would also accept descriptions like "готовый документ".
IMPERATIVE_RE = re.compile(
    r"\b(?:(?:под)?готов(?:ь|ьте)|сдела(?:й|йте)|организу(?:й|йте)|"
    r"обеспеч(?:ь|ьте)|выполн(?:и|ите)|направ(?:ь|ьте)|отправ(?:ь|ьте)|"
    r"согласу(?:й|йте)|провер(?:ь|ьте)|состав(?:ь|ьте)|предостав(?:ь|ьте))\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(?:не\s+позднее|до|к)\s+(\d{1,2})[./](\d{1,2})[./](20\d{2})\b",
    re.IGNORECASE,
)
DEADLINE_DATE_RE = re.compile(
    r"\bсрок(?:\s+(?:исполнения|выполнения|предоставления|поставки|оплаты))?\s*"
    r"(?:[:=\-–—]|установлен(?:\s+на)?)?\s*(\d{1,2})[./](\d{1,2})[./](20\d{2})\b",
    re.IGNORECASE,
)
MONTH_DATE_RE = re.compile(
    r"\b(?:не\s+позднее|до|к)\s+(\d{1,2})\s+"
    r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"(?:\s+(20\d{2}))?\b", re.IGNORECASE,
)
MONTHS = {name: index + 1 for index, name in enumerate(
    ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"))}
SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")
ADJACENT_DEADLINE_RE = re.compile(
    r"(?:срок(?:\s+(?:исполнения|выполнения|предоставления|поставки|оплаты))?\s*"
    r"[:=\-–—]?\s*|(?:не\s+позднее|до|к)\s+)"
    r"\d{1,2}[./]\d{1,2}[./]20\d{2}[.!?]?",
    re.IGNORECASE,
)
# A small set of Russian endings, not a dictionary of misspelled words. Ordinary
# short words (и, у, по, при, их, им, ей, её, etc.) deliberately do not participate.
# Two distinct unquoted fragments are needed: one fragment can be a name, game
# (го), technical notation or a typo. This is a review signal, never a repair.
ORPHAN_ENDING_RE = re.compile(
    r"(?<![\w/\-–—])(?:ый|ий|ая|яя|ое|ые|ую|юю|ых|ым|ть|ться|го)(?![\w/\-–—])"
)


def _has_repeated_orphan_endings(text: str) -> bool:
    if len(re.findall(r"\b[а-яё]{3,}\b", text, re.IGNORECASE)) < 5:
        return False
    fragments = set()
    for match in ORPHAN_ENDING_RE.finditer(text):
        before = text[:match.start()].rstrip()
        after = text[match.end():].lstrip()
        # Quoted words/suffixes and dot/colon abbreviations are not OCR evidence.
        if (before and before[-1] in "\"'«»„“”‘’") or (
            after and after[0] in "\"'«»„“”‘’.:"
        ):
            continue
        fragments.add(match.group())
    return len(fragments) >= 2


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
    """Flag corruption patterns; do not spell-correct, classify jargon or drop claims.

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
    if _has_repeated_orphan_endings(text):
        reasons.append(
            "В нескольких местах есть отдельно стоящие окончания слов; "
            "возможны разрывы распознанного текста. Сверьте цитату с документом."
        )
    return tuple(reasons)


def extract_explicit_due_date(sentence: str) -> date | None:
    """Extract only an explicit execution deadline, not a document reference date.

    Dates introduced by ``от`` or embedded in СНиП/ГОСТ references identify a
    source document.  Treating every date in an obligation sentence as a due
    date produced false, already-overdue tasks.  Absolute dates therefore need
    a local deadline marker such as ``до``, ``не позднее``, ``к`` or ``срок``.
    """
    match = DATE_RE.search(sentence) or DEADLINE_DATE_RE.search(sentence)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None
    word_match = MONTH_DATE_RE.search(sentence)
    if word_match:
        try:
            return date(
                int(word_match.group(3) or date.today().year),
                MONTHS[word_match.group(2).lower()],
                int(word_match.group(1)),
            )
        except ValueError:
            return None
    return None


def extract_task_candidates(text: str | None, limit: int = 5) -> list[TaskCandidate]:
    if not text:
        return []
    result: list[TaskCandidate] = []
    seen: set[str] = set()
    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in SENTENCE_RE.finditer(text):
        spans.append((start, boundary.start()))
        start = boundary.end()
    spans.append((start, len(text)))
    for index, (start, end) in enumerate(spans):
        raw = text[start:end]
        sentence = " ".join(raw.split()).strip(" -–—\t")
        if (len(sentence) < 18 or len(sentence) > 1200
                or not (OBLIGATION_RE.search(sentence) or IMPERATIVE_RE.search(sentence))):
            continue
        digest = hashlib.sha256(sentence.casefold().encode()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        due = extract_explicit_due_date(sentence)
        excerpt = sentence
        if due is None and index + 1 < len(spans):
            next_start, next_end = spans[index + 1]
            gap = text[end:next_start]
            next_sentence = " ".join(text[next_start:next_end].split()).strip()
            # Only the immediately following, standalone deadline clause may
            # supply a date. Never scan past prose, blank lines or signatures.
            if (len(gap) <= 3 and len(re.findall(r"\r\n|\r|\n", gap)) <= 1
                    and len(next_sentence) <= 80
                    and ADJACENT_DEADLINE_RE.fullmatch(next_sentence)):
                due = extract_explicit_due_date(next_sentence)
                if due is not None:
                    excerpt = text[start:next_end].strip()
        urgent = bool(re.search(r"\b(срочно|критич|немедленно|не позднее)\b", sentence, re.I))
        review_reasons = _text_quality_review_reasons(excerpt)
        # Compatibility scores for clean candidates; a valid date cannot override
        # damaged evidence. 0.45 is a conservative review ceiling, not 45% accuracy.
        confidence = 0.90 if due else 0.82
        if review_reasons:
            confidence = min(confidence, 0.45)
        result.append(TaskCandidate(sentence[:240], excerpt, due, "high" if urgent else "normal", confidence, review_reasons))
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
    # Deferred import: document_extraction calls back into this module's
    # extract_task_candidates for its regex fallback.
    from app.document_extraction import (
        FALLBACK_REASON_TEXT, extract_for_file, extract_for_files, match_assignee_hint,
    )

    default_assignee = _default_assignee(db, project_id)
    if not default_assignee:
        return []
    extract_for_files(db, files, project_id)  # no-op for files another engine already cached
    created: list[Task] = []
    for file in files:
        if file.is_folder:
            continue
        extraction = extract_for_file(db, file, project_id)
        for candidate in extraction.obligations:
            excerpt_hash = hashlib.sha256(candidate.excerpt.casefold().encode()).hexdigest()
            existing = db.scalar(select(Task.id).where(
                Task.project_id == project_id,
                Task.source_file_id == file.id,
                Task.source_excerpt_hash == excerpt_hash,
            ))
            if existing:
                continue
            matched_assignee = match_assignee_hint(db, project_id, candidate.assignee_hint)
            assignee = matched_assignee or default_assignee
            description = (
                f"Автоматически выделено из документа «{file.name}». "
                "Оценка эвристическая, не вероятность правильного распознавания. "
                "Требуется ручная проверка по исходной цитате."
                + (" Причины: " + " ".join(candidate.review_reasons) if candidate.review_reasons else "")
            )
            if candidate.assignee_hint and not matched_assignee:
                description += f" LLM предположил ответственного «{candidate.assignee_hint}» — не найден среди участников проекта."
            if extraction.extraction_method == "regex" and extraction.fallback_reason:
                reason_text = FALLBACK_REASON_TEXT.get(extraction.fallback_reason, extraction.fallback_reason)
                description += f" {reason_text}: ответственный и сумма не извлечены."
            task = Task(
                project_id=project_id,
                assignee_user_id=assignee.id,
                created_by_user_id=default_assignee.id,
                organizer_session_id=session_id,
                title=candidate.title,
                description=description,
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
                amount=candidate.amount,
                amount_currency=candidate.amount_currency,
                amount_evidence_quote=candidate.amount_evidence_quote,
                due_date_evidence_quote=candidate.due_date_evidence_quote,
                assignee_hint=candidate.assignee_hint,
                assignee_evidence_quote=candidate.assignee_evidence_quote,
                extraction_method=candidate.extraction_method,
            )
            db.add(task)
            db.flush()
            db.add(Obligation(
                project_id=project_id, owner_user_id=assignee.id, task_id=task.id,
                title=candidate.title, due_date=candidate.due_date,
                source_type=source_type, source_id=file.id, source_name=file.name,
                source_excerpt=candidate.excerpt, source_hash=excerpt_hash,
                confidence=candidate.confidence,
                amount=candidate.amount, amount_currency=candidate.amount_currency,
                amount_evidence_quote=candidate.amount_evidence_quote,
                due_date_evidence_quote=candidate.due_date_evidence_quote,
                assignee_hint=candidate.assignee_hint,
                assignee_evidence_quote=candidate.assignee_evidence_quote,
                extraction_method=candidate.extraction_method,
            ))
            created.append(task)
    db.commit()
    for task in created:
        db.refresh(task)
    return created
