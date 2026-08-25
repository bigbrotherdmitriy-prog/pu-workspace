from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.organizer_engine.types import DriveFile

REQUEST_RE = re.compile(r"\b(просим|прошу|запрашиваем|предоставьте|сообщите|подтвердите|согласуйте|направьте|уточните|дайте ответ|ожидаем ответ)\b", re.I)
SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")


@dataclass(slots=True)
class ResponseCandidate:
    subject: str
    body: str
    excerpt: str
    confidence: float


def extract_response_candidates(text: str | None, source_name: str, limit: int = 3) -> list[ResponseCandidate]:
    if not text:
        return []
    result: list[ResponseCandidate] = []
    seen: set[str] = set()
    for raw in SPLIT_RE.split(text):
        sentence = " ".join(raw.split()).strip(" -–—\t")
        explicit = bool(REQUEST_RE.search(sentence))
        if not (explicit or sentence.endswith("?")) or len(sentence) < 15 or len(sentence) > 1200:
            continue
        if sentence.casefold() in seen:
            continue
        seen.add(sentence.casefold())
        result.append(ResponseCandidate(
            f"Ответ на запрос из документа «{source_name}»"[:500],
            "Добрый день!\n\n"
            f"В ответ на ваш запрос: «{sentence}»\n\n"
            "Сообщаем, что запрос принят в работу. Подтверждённая информация и необходимые "
            "материалы будут направлены дополнительно после внутренней проверки.\n\n"
            "С уважением,\n[ФИО / должность]",
            sentence, 0.80 if explicit else 0.70,
        ))
        if len(result) >= limit:
            break
    return result


def _reviewer(db: Session, project_id: int) -> User | None:
    rows = db.execute(select(User, ProjectMember.role).join(ProjectMember, ProjectMember.user_id == User.id).where(ProjectMember.project_id == project_id)).all()
    order = {"owner": 0, "manager": 1, "editor": 2, "member": 3, "viewer": 4}
    if rows:
        rows.sort(key=lambda row: (order.get(row.role, 9), row.User.id))
        return rows[0].User
    return db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))


def create_response_drafts(db: Session, project_id: int, session_id: int, files: list[DriveFile]) -> list[ResponseDraft]:
    reviewer = _reviewer(db, project_id)
    if not reviewer:
        return []
    created: list[ResponseDraft] = []
    for file in files:
        if file.is_folder:
            continue
        for candidate in extract_response_candidates(file.content_text, file.name):
            digest = hashlib.sha256(candidate.excerpt.casefold().encode()).hexdigest()
            if db.scalar(select(ResponseDraft.id).where(ResponseDraft.project_id == project_id, ResponseDraft.source_file_id == file.id, ResponseDraft.source_excerpt_hash == digest)):
                continue
            draft = ResponseDraft(project_id=project_id, reviewer_user_id=reviewer.id, organizer_session_id=session_id, subject=candidate.subject, body=candidate.body, source_file_id=file.id, source_file_name=file.name, source_excerpt=candidate.excerpt, source_excerpt_hash=digest, confidence=candidate.confidence)
            db.add(draft); created.append(draft)
    db.commit()
    for draft in created: db.refresh(draft)
    return created
