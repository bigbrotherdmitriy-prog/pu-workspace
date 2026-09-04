import base64
import binascii
import hashlib
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.local_upload_staging import (
    LocalUploadAdmissionDenied,
    LocalUploadConflict,
    LocalUploadStagingError,
    LocalUploadUnavailable,
    UploadScope,
    admit_candidate,
    get_local_upload_runtime,
    stage_and_enqueue,
)
from app.models.user import User

router = APIRouter(prefix="/local-upload", tags=["local-upload"])
MAX_FILE_BYTES = int(os.getenv("LOCAL_UPLOAD_MAX_FILE_BYTES", str(10 * 1024 * 1024)))
MAX_BATCH_BYTES = int(os.getenv("LOCAL_UPLOAD_MAX_BATCH_BYTES", str(30 * 1024 * 1024)))
MAX_FILES = int(os.getenv("LOCAL_UPLOAD_MAX_FILES", "50"))


class LocalFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    mime_type: str = Field(default="application/octet-stream", max_length=255)
    content_base64: str


class LocalBatch(BaseModel):
    project_id: int
    files: list[LocalFile] = Field(min_length=1, max_length=MAX_FILES)


def _decoded_size(value: str) -> int:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_file_content")
    padding = len(value) - len(value.rstrip("="))
    if padding > 2 or len(value) % 4:
        raise ValueError("invalid_file_content")
    return (len(value) // 4) * 3 - padding


def decode_local_file(item: LocalFile) -> bytes:
    if _decoded_size(item.content_base64) > MAX_FILE_BYTES:
        raise ValueError("file_too_large")
    try:
        data = base64.b64decode(item.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid_file_content") from exc
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file_too_large")
    return data


@router.post("/analyze", status_code=202)
def analyze_local_folder(
    payload: LocalBatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # Authorization and whole-batch admission happen before base64 decode or
    # any staging read/write.
    require_project_role(db, user, payload.project_id, "manager")
    try:
        runtime = get_local_upload_runtime()
        estimated = [_decoded_size(item.content_base64) for item in payload.files]
        if any(size > runtime.max_file_bytes for size in estimated):
            raise LocalUploadAdmissionDenied("file_too_large")
        if sum(estimated) > MAX_BATCH_BYTES:
            raise LocalUploadAdmissionDenied("batch_too_large")
        decoded = [decode_local_file(item) for item in payload.files]
        candidates = [
            admit_candidate(
                item.path, item.mime_type, content,
                max_file_bytes=runtime.max_file_bytes,
                allowed_mime_types=runtime.allowed_mime_types,
            )
            for item, content in zip(payload.files, decoded, strict=True)
        ]
        request_key = idempotency_key or hashlib.sha256(
            b"\x00".join(
                (candidate.display_name + "\x00" + candidate.mime_type).encode()
                + hashlib.sha256(candidate.content).digest()
                for candidate in candidates
            )
        ).hexdigest()
        scope = UploadScope(owner_id=int(user.id), project_id=payload.project_id)
        jobs = [
            stage_and_enqueue(
                db, runtime=runtime, scope=scope, candidate=candidate,
                request_key=request_key, index=index,
            )
            for index, candidate in enumerate(candidates)
        ]
    except LocalUploadConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except LocalUploadUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except LocalUploadAdmissionDenied as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError as exc:
        detail = str(exc)
        if detail not in {"invalid_file_content", "file_too_large"}:
            detail = "invalid_file_content"
        raise HTTPException(422, detail) from None
    except LocalUploadStagingError:
        raise HTTPException(503, "local_upload_staging_unavailable") from None
    return {
        "status": "queued",
        "processed": 0,
        "skipped": [],
        "tasks": 0,
        "risks": 0,
        "decisions": 0,
        "drafts": 0,
        "documents": [],
        "jobs": [
            {"job_id": item.job_id, "staging_id": item.staging_id, "status": item.status}
            for item in jobs
        ],
    }
