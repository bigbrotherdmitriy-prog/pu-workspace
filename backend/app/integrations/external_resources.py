from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.external_resource import ExternalResourceLink


def record_external_resource(
    db: Session,
    *,
    project_id: int,
    entity_type: str,
    entity_id: int,
    provider: str,
    resource_type: str,
    external_id: str,
    container_id: str | None = None,
    sync_status: str = "synced",
    sync_error: str | None = None,
) -> ExternalResourceLink:
    link = db.scalar(select(ExternalResourceLink).where(
        ExternalResourceLink.entity_type == entity_type,
        ExternalResourceLink.entity_id == entity_id,
        ExternalResourceLink.provider == provider,
        ExternalResourceLink.resource_type == resource_type,
    ))
    if link is None:
        link = ExternalResourceLink(
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            provider=provider,
            resource_type=resource_type,
            external_id=external_id,
        )
        db.add(link)
    link.external_id = external_id
    link.container_id = container_id
    link.sync_status = sync_status
    link.sync_error = sync_error
    link.synced_at = datetime.now(timezone.utc)
    return link
