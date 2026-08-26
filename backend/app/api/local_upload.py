import base64
import binascii
import hashlib
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.core.notifications import notify_telegram
from app.database import get_db
from app.google_calendar import sync_tasks_to_calendar
from app.google_tasks import sync_tasks_to_google
from app.governance_engine import create_governance_items
from app.models.user import User
from app.organizer_engine.content import extract_text
from app.organizer_engine.types import DriveFile
from app.response_engine import create_response_drafts
from app.task_engine import create_tasks_from_files
from app.document_engine import index_documents

router = APIRouter(prefix="/local-upload", tags=["local-upload"])
MAX_FILE_BYTES = int(os.getenv("LOCAL_UPLOAD_MAX_FILE_BYTES", str(4 * 1024 * 1024)))
MAX_BATCH_BYTES = int(os.getenv("LOCAL_UPLOAD_MAX_BATCH_BYTES", str(20 * 1024 * 1024)))
MAX_FILES = int(os.getenv("LOCAL_UPLOAD_MAX_FILES", "50"))


class LocalFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    mime_type: str = Field(default="application/octet-stream", max_length=255)
    content_base64: str


class LocalBatch(BaseModel):
    project_id: int
    files: list[LocalFile] = Field(min_length=1, max_length=MAX_FILES)


def decode_local_file(item: LocalFile) -> bytes:
    try:
        data = base64.b64decode(item.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"Некорректное содержимое файла: {item.path}") from exc
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Файл больше {MAX_FILE_BYTES // 1024 // 1024} МБ: {item.path}")
    return data


@router.post("/analyze")
def analyze_local_folder(payload: LocalBatch, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
    total = 0
    extracted: list[DriveFile] = []
    skipped: list[dict] = []
    for item in payload.files:
        try:
            data = decode_local_file(item)
            total += len(data)
            if total > MAX_BATCH_BYTES:
                raise ValueError(f"Одна порция загрузки больше {MAX_BATCH_BYTES // 1024 // 1024} МБ")
            text = extract_text(data, item.mime_type, item.path)
            if not text:
                skipped.append({"path": item.path, "reason": "текст не извлечён"})
                continue
            content_digest = hashlib.sha256(data).hexdigest()
            path_digest = hashlib.sha256(item.path.casefold().encode()).hexdigest()
            extracted.append(DriveFile(id=f"local:{path_digest}", name=item.path, mime_type=item.mime_type, parent_id="local-upload", md5_checksum=content_digest, size=len(data), content_text=text))
        except ValueError as exc:
            skipped.append({"path": item.path, "reason": str(exc)})
    index_documents(db, payload.project_id, extracted, "local_upload")
    tasks = create_tasks_from_files(db, payload.project_id, None, extracted, source_type="local_upload")
    google_synced, _ = sync_tasks_to_google(db, payload.project_id, tasks)
    calendar_synced, _ = sync_tasks_to_calendar(db, payload.project_id, tasks)
    drafts = create_response_drafts(db, payload.project_id, None, extracted)
    risks, decisions = create_governance_items(db, payload.project_id, extracted, source_type="local_upload")
    if extracted:
        notify_telegram(
            f"PU Workspace: локальная рабочая папка — обработано файлов: {len(extracted)}; "
            f"задач: {len(tasks)}; рисков: {len(risks)}; решений: {len(decisions)}; ответов: {len(drafts)}."
        )
    return {
        "processed": len(extracted), "skipped": skipped, "tasks": len(tasks),
        "google_tasks": google_synced, "calendar": calendar_synced,
        "risks": len(risks), "decisions": len(decisions), "drafts": len(drafts),
    }
