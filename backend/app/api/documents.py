from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.models.document_version import DocumentVersion
from app.models.governance import Decision, Risk
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.core.auth import require_project_role, require_user


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

    documents = db.scalars(
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.id)
    ).all()

    return {
        "documents": [
            {
                "id": item.id,
                "name": item.name,
                "external_id": item.external_id,
                "mime_type": item.mime_type,
                "source": item.source,
                "status": item.status,
                "current_version": item.current_version,
                "source_modified_at": item.source_modified_at,
                "summary": item.summary,
            }
            for item in documents
        ]
    }


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
        "id": item.id, "name": item.name, "mime_type": item.mime_type, "source": item.source,
        "status": item.status, "current_version": item.current_version,
        "source_modified_at": item.source_modified_at, "summary": item.summary,
        "versions": [{"version": x.version_number, "created_at": x.created_at} for x in versions],
        "links": {"tasks": len(tasks), "risks": len(risks), "decisions": len(decisions), "drafts": len(drafts)},
    }
