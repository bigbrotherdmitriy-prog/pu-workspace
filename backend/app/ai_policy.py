import hashlib
import re

from sqlalchemy.orm import Session

from app.models.ai_policy import ProjectAIPolicy


class ExternalAIBlocked(RuntimeError):
    pass


SENSITIVE = [
    ("EMAIL", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("PHONE", re.compile(r"(?<!\d)(?:\+7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")),
    ("INN", re.compile(r"(?<!\d)(?:\d{10}|\d{12})(?!\d)")),
]


def policy_for_project(db: Session, project_id: int) -> ProjectAIPolicy | None:
    return db.get(ProjectAIPolicy, project_id)


def prepare_external_ai_text(db: Session, project_id: int, text: str) -> tuple[str, str]:
    policy = policy_for_project(db, project_id)
    mode = policy.mode if policy else "external_allowed"
    if mode == "local_only":
        raise ExternalAIBlocked("Внешний AI запрещён политикой проекта")
    if mode == "metadata_only":
        return f"Метаданные: длина текста {len(text)} символов. Содержимое политикой не передаётся.", mode
    if mode != "redacted":
        return text, mode
    result = text
    for kind, pattern in SENSITIVE:
        result = pattern.sub(lambda match: f"[{kind}_{hashlib.sha256(match.group().encode()).hexdigest()[:8]}]", result)
    return result, mode
