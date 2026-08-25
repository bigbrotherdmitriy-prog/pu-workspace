from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.user import User
from app.core.auth import require_admin, require_project_role, require_user


router = APIRouter(
    prefix="/history",
    tags=["history"],
)


class SnapshotRequest(BaseModel):
    content: str


def document_to_dict(document):
    result = {}

    for column in document.__table__.columns:
        value = getattr(document, column.name)

        if hasattr(value, "isoformat"):
            value = value.isoformat()

        result[column.name] = value

    return result


def accessible_document(db: Session, user: User, document_id: int, minimum: str):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    require_project_role(db, user, document.project_id, minimum)
    return document


@router.post("/documents/{document_id}/snapshot")
def create_snapshot(
    document_id: int,
    payload: SnapshotRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    document = accessible_document(db, user, document_id, "editor")

    current_max = db.scalar(
        select(
            func.max(DocumentVersion.version_number)
        ).where(
            DocumentVersion.document_id == document_id
        )
    )

    version_number = (current_max or 0) + 1

    version = DocumentVersion(
        document_id=document_id,
        version_number=version_number,
        content=payload.content,
    )

    db.add(version)
    db.flush()

    log = AuditLog(
        action="snapshot_created",
        entity_type="document",
        entity_id=document_id,
        details=f"version={version_number}",
    )

    db.add(log)
    db.commit()
    db.refresh(version)

    return {
        "status": "ok",
        "document": document_to_dict(document),
        "version": {
            "id": version.id,
            "document_id": version.document_id,
            "version_number": version.version_number,
            "content": version.content,
            "created_at": version.created_at,
        },
    }


@router.get("/documents/{document_id}/versions")
def get_versions(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    accessible_document(db, user, document_id, "viewer")

    versions = db.scalars(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id
        )
        .order_by(
            DocumentVersion.version_number.desc()
        )
    ).all()

    return {
        "document_id": document_id,
        "versions": [
            {
                "id": item.id,
                "version_number": item.version_number,
                "content": item.content,
                "created_at": item.created_at,
            }
            for item in versions
        ],
    }


@router.get("/documents/{document_id}/versions/{version_number}")
def get_version(
    document_id: int,
    version_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    accessible_document(db, user, document_id, "viewer")
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )

    if version is None:
        raise HTTPException(
            status_code=404,
            detail="Version not found",
        )

    return {
        "id": version.id,
        "document_id": version.document_id,
        "version_number": version.version_number,
        "content": version.content,
        "created_at": version.created_at,
    }


@router.post(
    "/documents/{document_id}/restore/{version_number}"
)
def restore_version(
    document_id: int,
    version_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    document = accessible_document(db, user, document_id, "manager")

    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )

    if version is None:
        raise HTTPException(
            status_code=404,
            detail="Version not found",
        )

    log = AuditLog(
        action="version_restored",
        entity_type="document",
        entity_id=document_id,
        details=f"restored_version={version_number}",
    )

    db.add(log)
    db.commit()

    return {
        "status": "restored",
        "document_id": document_id,
        "version_number": version_number,
        "content": version.content,
    }


@router.get("/audit")
def audit(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    limit = max(1, min(limit, 500))

    logs = db.scalars(
        select(AuditLog)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()

    return {
        "logs": [
            {
                "id": item.id,
                "action": item.action,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "details": item.details,
                "created_at": item.created_at,
            }
            for item in logs
        ]
    }
