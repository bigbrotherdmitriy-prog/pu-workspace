"""Read-only authority for binding meeting proposals to current local-upload evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.auth import ROLE_LEVEL
from app.models.materialization import Materialization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_pilot import (
    ConnectionIdentity, Evidence, SourceCurrent, SourceReference, SourceVersion,
)


class MeetingSourceDenied(RuntimeError):
    """Stable fail-closed result; source metadata never appears in the error."""


@dataclass(frozen=True, slots=True)
class AuthorizedMeetingSource:
    organization_id: int
    project_id: int
    source_id: str
    source_version_id: str
    evidence_id: str
    materialization_id: str


def _uuid(value: str) -> str:
    try:
        parsed = str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise MeetingSourceDenied("resource_unavailable") from None
    if parsed != value:
        raise MeetingSourceDenied("resource_unavailable")
    return value


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _materialization_locator_matches(locator: object, canonical: object,
                                     materialization_id: str) -> bool:
    if not isinstance(locator, dict) or not isinstance(canonical, dict):
        return False
    staging_id = locator.get("staging_id")
    try:
        derived_id = str(UUID(hex=staging_id)) if isinstance(staging_id, str) else None
    except ValueError:
        return False
    return (
        canonical == {"kind": "opaque_id", "value": staging_id, "normalization_version": "1"}
        and derived_id == materialization_id
    )


def require_current_local_upload_source(
    db,
    *,
    actor_user_id: int,
    project_id: int,
    source_id: str,
    source_version_id: str,
    evidence_id: str,
    materialization_id: str,
    lock: bool = True,
    now: datetime | None = None,
) -> AuthorizedMeetingSource:
    """Authorize one exact current observation without reading source bytes.

    This is deliberately narrower than the fragment reader. It accepts only the
    authoritative local-upload lineage already persisted by A05 and denies stale,
    purged, cross-project, cross-tenant, or viewer-selected sources.
    """

    if not db.in_transaction() or type(actor_user_id) is not int or type(project_id) is not int:
        raise MeetingSourceDenied("resource_unavailable")
    source_id = _uuid(source_id)
    source_version_id = _uuid(source_version_id)
    evidence_id = _uuid(evidence_id)
    materialization_id = _uuid(materialization_id)
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise MeetingSourceDenied("resource_unavailable")

    def one(model, *conditions):
        query = select(model).where(*conditions).execution_options(populate_existing=True)
        return db.scalar(query.with_for_update() if lock else query)

    project = one(Project, Project.id == project_id, Project.archived_at.is_(None))
    actor = db.get(User, actor_user_id)
    if project is None or actor is None:
        raise MeetingSourceDenied("resource_unavailable")
    if not actor.is_admin:
        membership = one(
            ProjectMember,
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == actor_user_id,
        )
        if membership is None or ROLE_LEVEL.get(membership.role, 0) < ROLE_LEVEL["manager"]:
            raise MeetingSourceDenied("resource_unavailable")

    source = one(
        SourceReference,
        SourceReference.id == source_id,
        SourceReference.organization_id == project.organization_id,
        SourceReference.origin_project_id == project_id,
    )
    version = one(
        SourceVersion,
        SourceVersion.id == source_version_id,
        SourceVersion.organization_id == project.organization_id,
        SourceVersion.source_id == source_id,
    )
    current = one(
        SourceCurrent,
        SourceCurrent.source_id == source_id,
        SourceCurrent.organization_id == project.organization_id,
    )
    evidence = one(
        Evidence,
        Evidence.id == evidence_id,
        Evidence.organization_id == project.organization_id,
        Evidence.source_id == source_id,
        Evidence.source_version_id == source_version_id,
    )
    materialization = one(
        Materialization,
        Materialization.id == materialization_id,
        Materialization.organization_id == project.organization_id,
        Materialization.project_id == project_id,
        Materialization.source_id == source_id,
        Materialization.source_version_id == source_version_id,
        Materialization.evidence_id == evidence_id,
        Materialization.parent_id.is_(None),
    )
    identity = one(
        ConnectionIdentity,
        ConnectionIdentity.id == source.identity_id,
        ConnectionIdentity.organization_id == project.organization_id,
    ) if source is not None else None

    locator = version.locator_at_observation if version is not None else None
    canonical = source.canonical_locator if source is not None else None
    retention_until = _utc(materialization.retention_until) if materialization is not None else None
    next_check_at = _utc(source.next_check_at) if source is not None else None
    if (
        source is None or version is None or current is None or evidence is None
        or materialization is None or identity is None
        or source.namespace != "local-upload" or source.object_kind != "file"
        or source.parent_source_id is not None or source.freshness != "fresh"
        or source.sync_state != "current" or source.availability != "available"
        or current.version_id != source_version_id or version.revision != 1
        or version.consistency != "digest_observed"
        or evidence.revision != 1 or evidence.locator != {"kind": "whole_object", "reason_code": "local_upload"}
        or materialization.state != "DERIVED" or not materialization.derive_allowed
        or materialization.copy_allowed or retention_until is None or retention_until <= checked_at
        or next_check_at is None or next_check_at <= checked_at
        or identity.provider != "local_upload" or identity.state != "verified"
        or identity.binding_epoch != 1 or identity.credential_generation != 1
        or not _materialization_locator_matches(locator, canonical, materialization_id)
        or source.policy_pins != evidence.policy_pins
        or not isinstance(source.policy_pins, dict)
        or set(source.policy_pins) != {"access", "retention", "residency"}
        or not isinstance(materialization.manifest, dict)
        or materialization.object_id != materialization.manifest.get("storage", {}).get("object_id")
    ):
        raise MeetingSourceDenied("resource_unavailable")

    return AuthorizedMeetingSource(
        organization_id=project.organization_id,
        project_id=project_id,
        source_id=source_id,
        source_version_id=source_version_id,
        evidence_id=evidence_id,
        materialization_id=materialization_id,
    )
