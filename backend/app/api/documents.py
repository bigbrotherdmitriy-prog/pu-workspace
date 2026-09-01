from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.models.document_version import DocumentVersion
from app.models.governance import Decision, Risk
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.job import BackgroundJob
from app.jobs.queue import enqueue, request_cancel
from app.core.auth import require_project_role, require_user
from app.integrations.source_urls import source_object_url


router = APIRouter(
    prefix="/projects",
    tags=["documents"],
)


class DocumentCreate(BaseModel):
    name: str
    external_id: str | None = None
    mime_type: str | None = None
    parent_external_id: str | None = None
    source: str = "google_drive"


class OcrBatchCreate(BaseModel):
    document_ids: list[int] | None = Field(default=None, max_length=500)


class OcrReviewUpdate(BaseModel):
    status: str = Field(pattern="^(confirmed|rejected)$")


@router.post("/{project_id}/documents")
def create_document(
    project_id: int,
    payload: DocumentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "editor")
    project = db.get(Project, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    document = Document(
        project_id=project_id,
        name=payload.name,
        external_id=payload.external_id,
        mime_type=payload.mime_type,
        parent_external_id=payload.parent_external_id,
        source=payload.source,
        status="discovered",
    )

    db.add(document)
    db.commit()
    db.refresh(document)

    return {
        "id": document.id,
        "project_id": document.project_id,
        "name": document.name,
        "external_id": document.external_id,
        "mime_type": document.mime_type,
        "status": document.status,
    }


@router.get("/{project_id}/documents")
def list_documents(
    project_id: int,
    search: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=50),
    source: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=100, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    project = db.get(Project, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    filters = [Document.project_id == project_id]
    if search:
        pattern = f"%{search.strip()}%"
        content_match = exists(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == Document.id,
                DocumentVersion.content.ilike(pattern),
            )
        )
        filters.append(
            or_(
                Document.name.ilike(pattern),
                Document.summary.ilike(pattern),
                Document.notes.ilike(pattern),
                content_match,
            )
        )
    if status:
        filters.append(Document.status == status)
    if source:
        filters.append(Document.source == source)
    total = db.scalar(select(func.count(Document.id)).where(*filters)) or 0
    documents = db.scalars(
        select(Document).where(*filters).order_by(Document.id.desc()).offset(offset).limit(limit)
    ).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "documents": [
            {
                "id": item.id,
                "name": item.name,
                "external_id": item.external_id,
                "parent_external_id": item.parent_external_id,
                "source_url": source_object_url(item.source, item.external_id),
                "mime_type": item.mime_type,
                "source": item.source,
                "status": item.status,
                "current_version": item.current_version,
                "source_modified_at": item.source_modified_at,
                "summary": item.summary,
                "extraction_method": item.extraction_method,
                "extraction_quality": item.extraction_quality,
                "ocr_pages": item.ocr_pages,
                "ocr_confidence": item.ocr_confidence,
                "ocr_review_status": item.ocr_review_status,
                "ocr_updated_at": item.ocr_updated_at,
            }
            for item in documents
        ]
    }


@router.get("/{project_id}/documents/ocr-review")
def list_ocr_review_queue(
    project_id: int, limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    rows = list(db.scalars(select(Document).where(
        Document.project_id == project_id,
        Document.ocr_review_status == "needs_review",
    ).order_by(Document.ocr_confidence.asc(), Document.id.asc()).limit(limit)))
    return {
        "count": len(rows),
        "documents": [
            {
                "id": item.id, "name": item.name,
                "confidence": item.ocr_confidence,
                "review_status": item.ocr_review_status,
                "evidence": item.ocr_metadata or {},
            }
            for item in rows
        ],
    }


@router.post("/{project_id}/documents/{document_id}/ocr-review")
def update_ocr_review(
    project_id: int, document_id: int, payload: OcrReviewUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    item = db.get(Document, document_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(404, "Document not found")
    if item.ocr_review_status not in {"needs_review", "confirmed", "rejected"}:
        raise HTTPException(409, "Document does not require OCR review")
    item.ocr_review_status = payload.status
    db.commit()
    return {"document_id": item.id, "review_status": item.ocr_review_status}


@router.get("/{project_id}/documents/{document_id}")
def document_card(project_id: int, document_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    item = db.get(Document, document_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(404, "Document not found")
    versions = list(db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == item.id).order_by(DocumentVersion.version_number.desc())).all())
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id, Task.source_file_id == item.external_id)).all())
    risks = list(db.scalars(select(Risk).where(Risk.project_id == project_id, Risk.source_id == item.external_id)).all())
    decisions = list(db.scalars(select(Decision).where(Decision.project_id == project_id, Decision.source_id == item.external_id)).all())
    drafts = list(db.scalars(select(ResponseDraft).where(ResponseDraft.project_id == project_id, ResponseDraft.source_file_id == item.external_id)).all())
    return {
        "id": item.id, "name": item.name, "external_id": item.external_id,
        "source_url": source_object_url(item.source, item.external_id),
        "mime_type": item.mime_type, "source": item.source,
        "status": item.status, "current_version": item.current_version,
        "source_modified_at": item.source_modified_at, "summary": item.summary,
        "extraction_method": item.extraction_method,
        "extraction_quality": item.extraction_quality,
        "ocr_pages": item.ocr_pages,
        "ocr_confidence": item.ocr_confidence,
        "ocr_review_status": item.ocr_review_status,
        "ocr_metadata": item.ocr_metadata,
        "ocr_updated_at": item.ocr_updated_at,
        "versions": [{"version": x.version_number, "created_at": x.created_at} for x in versions],
        "links": {"tasks": len(tasks), "risks": len(risks), "decisions": len(decisions), "drafts": len(drafts)},
    }


@router.post("/{project_id}/documents/ocr-batches")
def create_ocr_batch(
    project_id: int, payload: OcrBatchCreate,
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    document_ids = sorted(set(payload.document_ids or [])) or None
    if document_ids:
        found = set(db.scalars(select(Document.id).where(
            Document.project_id == project_id, Document.id.in_(document_ids),
        )))
        if found != set(document_ids):
            raise HTTPException(404, "One or more documents were not found in this project")
    active = db.scalar(select(BackgroundJob).where(
        BackgroundJob.kind == "documents.ocr",
        BackgroundJob.status.in_(("queued", "retrying", "running")),
        BackgroundJob.payload["project_id"].as_integer() == project_id,
    ).order_by(BackgroundJob.id.desc()))
    if active is not None:
        return {"job_id": active.id, "status": active.status, "already_running": True}
    job = enqueue(
        db, "documents.ocr", {"project_id": project_id, "document_ids": document_ids},
        priority=60, max_attempts=2,
    )
    job.payload = {**dict(job.payload or {}), "job_id": job.id}
    db.commit()
    return {"job_id": job.id, "status": job.status, "already_running": False}


@router.get("/{project_id}/documents/ocr-batches/{job_id}")
def get_ocr_batch(
    project_id: int, job_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    job = db.get(BackgroundJob, job_id)
    if job is None or job.kind != "documents.ocr" or int(job.payload.get("project_id", -1)) != project_id:
        raise HTTPException(404, "OCR batch not found")
    result = dict(job.result or {})
    effective_status = "cancelled" if result.get("cancelled") else job.status
    return {
        # Keep the existing OCR panel compatible while the canonical admin
        # queue contract uses `completed`.
        "job_id": job.id,
        "status": "succeeded" if effective_status == "completed" else effective_status,
        "attempts": job.attempts, "progress": job.progress,
        "duration_ms": job.duration_ms, "worker_id": job.worker_id,
        "result": job.result,
        "error": job.last_error if job.status in {"failed", "dead_letter"} else None,
        "created_at": job.created_at, "updated_at": job.updated_at,
    }


@router.post("/{project_id}/documents/ocr-batches/{job_id}/cancel")
def cancel_ocr_batch(
    project_id: int, job_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    job = db.get(BackgroundJob, job_id)
    if job is None or job.kind != "documents.ocr" or int(job.payload.get("project_id", -1)) != project_id:
        raise HTTPException(404, "OCR batch not found")
    status = request_cancel(db, job.id, allow_running=True)
    if status not in {"cancelled", "cancellation_requested", "completed", "dead_letter"}:
        raise HTTPException(409, "OCR batch cannot be cancelled")
    return {"job_id": job.id, "status": status}
