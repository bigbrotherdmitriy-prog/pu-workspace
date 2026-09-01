from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.document_engine import index_documents
from app.governance_engine import create_governance_items
from app.models.document import Document
from app.models.job import BackgroundJob
from app.organizer_engine.content import extract_text_result
from app.organizer_engine.drive import DriveClient
from app.organizer_engine.drive_factory import get_drive_service
from app.organizer_engine.types import DriveFile
from app.response_engine import create_response_drafts
from app.task_engine import create_tasks_from_files


SUPPORTED_SUFFIXES = {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"}
MAX_OCR_FILE_BYTES = int(os.getenv("OCR_MAX_FILE_BYTES", str(25 * 1024 * 1024)))


def _supported(document: Document) -> bool:
    suffix = document.name.lower().rsplit(".", 1)[-1] if "." in document.name else ""
    return document.mime_type == "application/pdf" or bool(
        (document.mime_type or "").startswith("image/") or suffix in SUPPORTED_SUFFIXES
    )


def _job_control(db, job_id: int | None, *, completed: int, total: int, document_id: int | None = None) -> bool:
    """Publish bounded progress and read cooperative cancellation without changing queue internals."""
    if job_id is None:
        return False
    job = db.get(BackgroundJob, job_id)
    if job is None:
        return True
    current = dict(job.result or {})
    cancel_requested = bool(current.get("cancel_requested")) or job.status == "cancelled"
    job.result = {
        "progress": {
            "completed": completed, "total": total,
            "percent": round((completed / total) * 100) if total else 100,
            "document_id": document_id,
        },
        "cancel_requested": cancel_requested,
    }
    db.commit()
    return cancel_requested


def reprocess_documents(project_id: int, document_ids: list[int] | None = None, job_id: int | None = None) -> dict:
    """Re-OCR originals through the configured StorageAdapter; never mutate them."""
    with SessionLocal() as db:
        filters = [Document.project_id == project_id]
        if document_ids:
            filters.append(Document.id.in_(document_ids))
        documents = list(db.scalars(select(Document).where(*filters).order_by(Document.id)))
        drive: DriveClient | None = None
        processed: list[dict] = []
        skipped: list[dict] = []
        extracted: list[DriveFile] = []
        automation_ready: list[DriveFile] = []
        by_external_id: dict[str, tuple[Document, object]] = {}
        cancelled = _job_control(db, job_id, completed=0, total=len(documents))
        for index, document in enumerate(documents):
            if cancelled or _job_control(db, job_id, completed=index, total=len(documents), document_id=document.id):
                cancelled = True
                break
            if not _supported(document):
                skipped.append({"id": document.id, "name": document.name, "reason": "format_not_supported"})
                continue
            if not document.external_id or document.source not in {"google_drive", "google_drive_copy"}:
                skipped.append({"id": document.id, "name": document.name, "reason": "original_not_available"})
                continue
            try:
                drive = drive or DriveClient(get_drive_service(project_id=project_id, db=db))
                data, mime_type = drive.read_bytes(document.external_id, MAX_OCR_FILE_BYTES)
                result = extract_text_result(data, mime_type, document.name)
                if not result.text:
                    skipped.append({"id": document.id, "name": document.name, "reason": "text_not_recognized"})
                    continue
                item = DriveFile(
                    id=document.external_id, name=document.name,
                    mime_type=mime_type, parent_id=document.parent_external_id or "",
                    content_text=result.text,
                )
                extracted.append(item)
                if not result.needs_review:
                    automation_ready.append(item)
                by_external_id[document.external_id] = (document, result)
                processed.append({
                    "id": document.id, "name": document.name, "method": result.method,
                    "quality": result.quality, "ocr_pages": result.ocr_pages,
                    "total_pages": result.total_pages, "confidence": result.confidence,
                    "needs_review": result.needs_review, "warnings": result.warnings,
                })
            except Exception as exc:
                skipped.append({"id": document.id, "name": document.name, "reason": exc.__class__.__name__})

        if extracted:
            indexed = index_documents(db, project_id, extracted, "google_drive")
            for document in indexed:
                pair = by_external_id.get(document.external_id or "")
                if pair:
                    _, result = pair
                    document.extraction_method = result.method
                    document.extraction_quality = result.quality
                    document.ocr_pages = result.ocr_pages
                    document.ocr_confidence = result.confidence
                    document.ocr_review_status = "needs_review" if result.needs_review else "not_required"
                    document.ocr_metadata = result.metadata()
                    document.ocr_updated_at = datetime.now(timezone.utc)
            # Low-confidence OCR is indexed for review, but cannot create legal or financial actions.
            tasks = create_tasks_from_files(db, project_id, None, automation_ready, source_type="ocr_reprocess")
            drafts = create_response_drafts(db, project_id, None, automation_ready)
            risks, decisions = create_governance_items(db, project_id, automation_ready, source_type="ocr_reprocess")
            db.commit()
        else:
            tasks, drafts, risks, decisions = [], [], [], []
        _job_control(db, job_id, completed=len(processed) + len(skipped), total=len(documents))
        return {
            "total": len(documents), "processed": processed, "skipped": skipped,
            "cancelled": cancelled,
            "tasks": len(tasks), "drafts": len(drafts), "risks": len(risks), "decisions": len(decisions),
        }
