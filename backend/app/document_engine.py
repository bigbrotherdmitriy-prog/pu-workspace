from __future__ import annotations

import hashlib
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.organizer_engine.types import DriveFile


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def index_documents(db: Session, project_id: int, files: list[DriveFile], source: str) -> list[Document]:
    indexed: list[Document] = []
    for file in files:
        if file.is_folder:
            continue
        content = file.content_text or ""
        content_hash = hashlib.sha256(content.encode()).hexdigest() if content else (file.md5_checksum or None)
        document = db.scalar(select(Document).where(Document.project_id == project_id, Document.external_id == file.id))
        if document is None:
            document = Document(project_id=project_id, external_id=file.id, name=file.name, mime_type=file.mime_type, parent_external_id=file.parent_id, source=source, status="analyzed" if content else "discovered", content_hash=content_hash, source_modified_at=_parse_time(file.modified_time), summary=content[:700] or None, current_version=1)
            db.add(document)
            db.flush()
            if content:
                db.add(DocumentVersion(document_id=document.id, version_number=1, content=content))
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
                db.add(DocumentVersion(document_id=document.id, version_number=document.current_version, content=content))
        indexed.append(document)
    db.commit()
    return indexed
