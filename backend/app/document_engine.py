from __future__ import annotations

import hashlib
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.core.integration_types import StorageObject


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def index_documents(
    db: Session,
    project_id: int,
    files: list[StorageObject],
    source: str,
    *,
    exact_source_versions: dict[str, str] | None = None,
) -> list[Document]:
    indexed: list[Document] = []
    for file in files:
        if file.is_folder:
            continue
        content = file.content_text or ""
        content_hash = hashlib.sha256(content.encode()).hexdigest() if content else (file.md5_checksum or None)
        document = db.scalar(select(Document).where(
            Document.project_id == project_id,
            Document.source == source,
            Document.external_id == file.id,
        ))
        document_version = None
        if document is None:
            document = Document(project_id=project_id, external_id=file.id, name=file.name, mime_type=file.mime_type, parent_external_id=file.parent_id, source=source, status="analyzed" if content else "discovered", content_hash=content_hash, source_modified_at=_parse_time(file.modified_time), summary=content[:700] or None, current_version=1)
            db.add(document)
            db.flush()
            if content:
                document_version = DocumentVersion(document_id=document.id, version_number=1, content=content)
                db.add(document_version)
                db.flush()
        else:
            changed = bool(content and content_hash != document.content_hash)
            document.name = file.name
            document.mime_type = file.mime_type
            document.parent_external_id = file.parent_id
            document.source_modified_at = _parse_time(file.modified_time) or document.source_modified_at
            if changed:
                latest = db.scalar(select(func.max(DocumentVersion.version_number)).where(DocumentVersion.document_id == document.id)) or 0
                document.current_version = latest + 1
                document.content_hash = content_hash
                document.summary = content[:700]
                document.status = "analyzed"
                document_version = DocumentVersion(
                    document_id=document.id,
                    version_number=document.current_version,
                    content=content,
                )
                db.add(document_version)
                db.flush()
            elif content:
                document_version = db.scalar(select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.version_number == document.current_version,
                ))
        if content and document_version is not None:
            from app.source_evidence.legacy_ingestion import bind_legacy_document_version
            bind_legacy_document_version(
                db,
                project_id=project_id,
                document=document,
                document_version=document_version,
                item=file,
                source=source,
                exact_source_version_id=(exact_source_versions or {}).get(file.id),
            )
        indexed.append(document)
    db.commit()
    return indexed
