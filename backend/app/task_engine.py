from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.organizer_engine.types import DriveFile

OBLIGATION_RE = re.compile(
    r"\b(должен|должна|должны|обязан|обязана|обязаны|необходимо|следует|"
    r"поручить|поручено|предоставить|подготовить|согласовать|направить|"
    r"выполнить|устранить|оплатить|поставить)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b(?:до\s+|не позднее\s+)?(\d{1,2})[./](\d{1,2})[./](20\d{2})\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")


@dataclass(slots=True)
class TaskCandidate:
    title: str
    excerpt: str
    due_date: date | None
    priority: str
    confidence: float


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
        urgent = bool(re.search(r"\b(срочно|критич|немедленно|не позднее)\b", sentence, re.I))
        confidence = 0.90 if due else 0.82
        result.append(TaskCandidate(sentence[:240], sentence, due, "high" if urgent else "normal", confidence))
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


def create_tasks_from_files(db: Session, project_id: int, session_id: int | None, files: list[DriveFile], source_type: str = "document_analysis") -> list[Task]:
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
                description=f"Автоматически выделено из документа «{file.name}».",
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
            created.append(task)
    db.commit()
    for task in created:
        db.refresh(task)
    return created
