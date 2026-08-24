from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import Document
from app.models.project import Project


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
):
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
):
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
            }
            for item in documents
        ]
    }
