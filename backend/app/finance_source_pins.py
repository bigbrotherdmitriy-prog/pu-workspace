from dataclasses import dataclass
import hashlib

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_version import DocumentVersion


@dataclass(frozen=True)
class DocumentPin:
    document: Document
    version: DocumentVersion
    sha256: str


def resolve_current_document_pin(
    db: Session,
    project_id: int,
    document_id: int,
    expected_version_id: int | None = None,
    expected_sha256: str | None = None,
    *,
    missing_status: int = 422,
    missing_detail: str = "Документ не принадлежит выбранному проекту",
) -> DocumentPin:
    """Return the exact current DocumentVersion and its canonical text hash."""
    document = db.scalar(select(Document).where(
        Document.id == document_id,
        Document.project_id == project_id,
    ))
    if document is None:
        raise HTTPException(missing_status, missing_detail)
    version = db.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == document.id,
        DocumentVersion.version_number == document.current_version,
    ))
    if version is None:
        raise HTTPException(409, "У документа нет доступной текущей версии")
    digest = hashlib.sha256((version.content or "").encode("utf-8")).hexdigest()
    if expected_version_id is not None and expected_version_id != version.id:
        raise HTTPException(409, "SOURCE_VERSION_MISMATCH: версия документа изменилась")
    if expected_sha256 is not None and expected_sha256 != digest:
        raise HTTPException(409, "SOURCE_VERSION_MISMATCH: содержимое документа изменилось")
    return DocumentPin(document=document, version=version, sha256=digest)


def assert_document_pin_current(
    db: Session,
    project_id: int,
    document_id: int,
    version_id: int | None,
    sha256: str | None,
) -> DocumentPin:
    if version_id is None or sha256 is None:
        raise HTTPException(
            409,
            "SOURCE_PIN_MISSING: запись связана с документом, но версия источника не закреплена",
        )
    return resolve_current_document_pin(
        db,
        project_id,
        document_id,
        expected_version_id=version_id,
        expected_sha256=sha256,
    )
