from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Session

from database import Base, get_db


router = APIRouter(prefix="/documents", tags=["documents"])


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id = Column(String(255), index=True)
    name = Column(String(500), nullable=False)
    mime_type = Column(String(255))
    parent_external_id = Column(String(255))
    source = Column(String(50), nullable=False, default="manual")
    status = Column(String(50), nullable=False, default="active")
    notes = Column(Text)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_version_id = Column(String(255))
    content_text = Column(Text)
    checksum = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class DocumentCreate(BaseModel):
    project_id: int
    name: str
    external_id: str | None = None
    mime_type: str | None = None
    parent_external_id: str | None = None
    source: str = "manual"
    status: str = "active"
    notes: str | None = None


class VersionCreate(BaseModel):
    content_text: str | None = None
    external_version_id: str | None = None
    checksum: str | None = None


def document_dict(item):
    return {
        "id": item.id,
        "project_id": item.project_id,
        "external_id": item.external_id,
        "name": item.name,
        "mime_type": item.mime_type,
        "parent_external_id": item.parent_external_id,
        "source": item.source,
        "status": item.status,
        "notes": item.notes,
    }


@router.get("/")
def list_documents(project_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Document).order_by(Document.id)

    if project_id is not None:
        query = query.where(Document.project_id == project_id)

    items = db.scalars(query).all()

    return {
        "documents": [document_dict(item) for item in items]
    }


@router.get("/{document_id}")
def get_document(document_id: int, db: Session = Depends(get_db)):
    item = db.get(Document, document_id)

    if item is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return document_dict(item)


@router.post("/")
def create_document(payload: DocumentCreate, db: Session = Depends(get_db)):
    item = Document(**payload.model_dump())

    db.add(item)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(item)

    return document_dict(item)


@router.delete("/{document_id}")
def delete_document(document_id: int, db: Session = Depends(get_db)):
    item = db.get(Document, document_id)

    if item is None:
        raise HTTPException(status_code=404, detail="Document not found")

    db.delete(item)
    db.commit()

    return {
        "status": "deleted",
        "id": document_id,
    }


@router.post("/{document_id}/versions")
def create_version(
    document_id: int,
    payload: VersionCreate,
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    version = DocumentVersion(
        document_id=document_id,
        **payload.model_dump(),
    )

    db.add(version)
    db.commit()
    db.refresh(version)

    return {
        "id": version.id,
        "document_id": version.document_id,
        "external_version_id": version.external_version_id,
        "content_text": version.content_text,
        "checksum": version.checksum,
        "created_at": (
            version.created_at.isoformat()
            if version.created_at
            else None
        ),
    }


@router.get("/{document_id}/versions")
def list_versions(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    versions = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.id)
    ).all()

    return {
        "versions": [
            {
                "id": item.id,
                "document_id": item.document_id,
                "external_version_id": item.external_version_id,
                "content_text": item.content_text,
                "checksum": item.checksum,
                "created_at": (
                    item.created_at.isoformat()
                    if item.created_at
                    else None
                ),
            }
            for item in versions
        ]
    }
