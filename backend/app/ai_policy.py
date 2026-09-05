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
    return _redact_sensitive(text), mode


def _redact_sensitive(text: str) -> str:
    result = text
    for kind, pattern in SENSITIVE:
        result = pattern.sub(lambda match: f"[{kind}_{hashlib.sha256(match.group().encode()).hexdigest()[:8]}]", result)
    return result


def prepare_external_ai_document(
    db: Session, project_id: int, text: str, filename: str,
) -> tuple[str, str, str]:
    """Apply the same project policy to every document prompt field.

    The caller retains the original filename locally and uses the prepared
    filename both as provider context and as the cache context.
    """
    prepared_text, mode = prepare_external_ai_text(db, project_id, text)
    if mode == "metadata_only":
        prepared_filename = "document"
    elif mode == "redacted":
        prepared_filename = _redact_sensitive(filename)
    else:
        prepared_filename = filename
    return prepared_text, prepared_filename, mode
