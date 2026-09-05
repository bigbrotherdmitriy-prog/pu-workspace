"""Bridge legacy document indexing to exact v5.4 source observations.

The bridge stores metadata and digests only.  It never reads a provider, copies
an original, or puts document content in a queue payload.  Missing connection or
provider-version metadata leaves the legacy version deliberately unbound so all
legal/financial consumers continue to fail closed and request human review.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from uuid import UUID, uuid5

from sqlalchemy import select

from app.core.integration_types import StorageObject
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.drive_connection import DriveConnection
from app.models.project import Project
from app.models.v54_pilot import ConnectionIdentity, SourceCurrent, SourceReference, SourceVersion


_NAMESPACE = UUID("7ef94418-8ac1-48d4-8d75-638ca6f31174")
_STORAGE_PROVIDERS = frozenset({"google_drive", "yandex_disk"})


class LegacyIngestionBindingConflict(ValueError):
    """A content-free error for an attempted ambiguous or mutable binding."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(kind: str, value: str) -> str:
    return str(uuid5(_NAMESPACE, f"{kind}\x00{value}"))


def _provider(value: str | None) -> str | None:
    if value == "google_workspace":
        return "google_drive"
    return value if value in _STORAGE_PROVIDERS else None


def _provider_from_source(source: str) -> str | None:
    if source == "google_drive" or source.startswith("google_drive_"):
        return "google_drive"
    if source == "yandex_disk" or source.startswith("yandex_disk_"):
        return "yandex_disk"
    return None


def _observed_at(value: str | None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _exact_parent(db, *, project: Project, source_version_id: str | None):
    if source_version_id is None:
        return None
    try:
        if str(UUID(source_version_id)) != source_version_id:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise LegacyIngestionBindingConflict("exact_source_version_unavailable") from None
    version = db.scalar(select(SourceVersion).where(
        SourceVersion.id == source_version_id,
        SourceVersion.organization_id == project.organization_id,
    ).with_for_update())
    source = db.get(SourceReference, version.source_id) if version else None
    current = db.get(SourceCurrent, version.source_id) if version else None
    if (
        version is None
        or source is None
        or current is None
        or source.organization_id != project.organization_id
        or source.origin_project_id != project.id
        or current.organization_id != project.organization_id
        or current.version_id != version.id
        or source.availability != "available"
        or not isinstance(source.policy_pins, dict)
        or not source.policy_pins
        or not isinstance(source.residency, dict)
        or not source.residency
    ):
        raise LegacyIngestionBindingConflict("exact_source_version_unavailable")
    return source, version


def _storage_identity(db, *, project: Project, item: StorageObject, source: str):
    provider = _provider_from_source(source)
    if provider is None or (_provider(item.provider) if item.provider else provider) != provider:
        return None
    connection = db.scalar(select(DriveConnection).where(
        DriveConnection.project_id == project.id,
    ).with_for_update())
    if connection is None or connection.status != "connected" or _provider(connection.provider) != provider:
        return None
    account_signal = connection.connection_id or connection.account_email
    revision_signal = item.md5_checksum or item.modified_time
    if not account_signal or not revision_signal:
        return None
    account_key = _digest(
        f"{project.organization_id}:{provider}:{connection.id}:{account_signal}"
    )
    identity = db.scalar(select(ConnectionIdentity).where(
        ConnectionIdentity.organization_id == project.organization_id,
        ConnectionIdentity.provider == provider,
        ConnectionIdentity.account_key == account_key,
    ).with_for_update())
    if identity is None:
        identity = ConnectionIdentity(
            id=_stable_id("identity", account_key),
            organization_id=project.organization_id,
            provider=provider,
            account_key=account_key,
            state="verified",
            binding_epoch=1,
            record_version=1,
            credential_generation=1,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(identity)
        db.flush()
    elif identity.state != "verified" or identity.binding_epoch <= 0:
        raise LegacyIngestionBindingConflict("exact_source_version_unavailable")
    policy_pins = {
        "access": {"kind": "project_acl", "project_id": project.id},
        "retention": {"kind": "legacy_document"},
        "residency": {"kind": "provider_managed", "provider": provider},
    }
    residency = {"source_location": provider, "assurance": "legacy_ingestion_bridge"}
    return identity, None, provider, revision_signal, policy_pins, residency


def _representation_source(
    db,
    *,
    project: Project,
    document: Document,
    item: StorageObject,
    source: str,
    exact_source_version_id: str | None,
):
    parent = _exact_parent(
        db, project=project, source_version_id=exact_source_version_id,
    ) if exact_source_version_id else None
    if parent:
        parent_source, parent_version = parent
        identity = db.get(ConnectionIdentity, parent_source.identity_id)
        if identity is None or identity.organization_id != project.organization_id:
            raise LegacyIngestionBindingConflict("exact_source_version_unavailable")
        provider = identity.provider
        revision_signal = parent_version.observation_key
        policy_pins = dict(parent_source.policy_pins)
        residency = dict(parent_source.residency)
        parent_source_id = parent_source.id
        # Parent linkage is intentionally inside the same provider namespace;
        # the model constraint rejects cross-namespace ancestry.
        namespace = parent_source.namespace
    else:
        storage = _storage_identity(db, project=project, item=item, source=source)
        if storage is None:
            return None
        identity, parent_source_id, provider, revision_signal, policy_pins, residency = storage
        namespace = f"legacy-document:{provider}"

    external_key = _digest(
        f"{project.organization_id}:{project.id}:{identity.id}:{source}:{item.id}"
    )
    source_id = _stable_id("source", external_key)
    row = db.scalar(select(SourceReference).where(
        SourceReference.id == source_id,
        SourceReference.organization_id == project.organization_id,
    ).with_for_update())
    expected_locator = {
        "kind": "opaque_digest",
        "value": external_key,
        "normalization_version": "1",
    }
    if row is None:
        row = SourceReference(
            id=source_id,
            organization_id=project.organization_id,
            origin_project_id=project.id,
            identity_id=identity.id,
            parent_source_id=parent_source_id,
            namespace=namespace,
            external_id=external_key,
            external_id_kind="sha256",
            incarnation=1,
            object_kind="file",
            canonical_locator=expected_locator,
            record_version=1,
            freshness="fresh",
            sync_state="current",
            availability="available",
            last_seen_at=_observed_at(item.modified_time),
            last_checked_at=_observed_at(item.modified_time),
            policy_pins=policy_pins,
            residency=residency,
        )
        db.add(row)
        db.flush()
    elif (
        row.origin_project_id != project.id
        or row.identity_id != identity.id
        or row.parent_source_id != parent_source_id
        or row.namespace != namespace
        or row.external_id != external_key
        or row.object_kind != "file"
        or row.canonical_locator != expected_locator
        or row.policy_pins != policy_pins
        or row.residency != residency
    ):
        raise LegacyIngestionBindingConflict("immutable_source_conflict")
    return row, provider, revision_signal


def bind_legacy_document_version(
    db,
    *,
    project_id: int,
    document: Document,
    document_version: DocumentVersion,
    item: StorageObject,
    source: str,
    exact_source_version_id: str | None = None,
) -> SourceVersion | None:
    """Create one immutable exact observation or leave the version unbound."""
    project = db.scalar(select(Project).where(
        Project.id == project_id,
        Project.organization_id.is_not(None),
    ).with_for_update())
    if (
        project is None
        or document.project_id != project.id
        or document_version.document_id != document.id
        or document.current_version != document_version.version_number
        or not item.content_text
        or document_version.content != item.content_text
    ):
        return None
    representation = _representation_source(
        db,
        project=project,
        document=document,
        item=item,
        source=source,
        exact_source_version_id=exact_source_version_id,
    )
    if representation is None:
        return None
    source_row, provider, revision_signal = representation
    content_digest = _digest(document_version.content)
    revision_digest = _digest(str(revision_signal))
    observation_key = _digest(f"{source_row.id}:{revision_digest}:{content_digest}")
    version_id = _stable_id("version", f"{source_row.id}:{observation_key}")
    locator = {
        "kind": "legacy_document_version",
        "document_id": document.id,
        "document_version_id": document_version.id,
        "provider": provider,
        "source": source,
    }
    integrity = [{"algorithm": "sha256", "value": content_digest}]
    version = db.scalar(select(SourceVersion).where(
        SourceVersion.organization_id == project.organization_id,
        SourceVersion.source_id == source_row.id,
        SourceVersion.observation_key == observation_key,
    ).with_for_update())
    if version is None:
        version = SourceVersion(
            id=version_id,
            organization_id=project.organization_id,
            source_id=source_row.id,
            revision=1,
            observation_key=observation_key,
            provider_revision=revision_digest,
            consistency="digest_observed",
            locator_at_observation=locator,
            integrity=integrity,
            observed_at=_observed_at(item.modified_time),
            legacy_document_version_id=document_version.id,
        )
        db.add(version)
        db.flush()
        db.add(AuditLog(
            action="v54.legacy_document_version_bound",
            entity_type="document_version",
            entity_id=document_version.id,
            details=f"provider={provider}; source={source}",
        ))
    elif (
        version.id != version_id
        or version.provider_revision != revision_digest
        or version.consistency != "digest_observed"
        or version.locator_at_observation != locator
        or version.integrity != integrity
        or version.legacy_document_version_id != document_version.id
    ):
        raise LegacyIngestionBindingConflict("immutable_source_version_conflict")

    current = db.scalar(select(SourceCurrent).where(
        SourceCurrent.source_id == source_row.id,
        SourceCurrent.organization_id == project.organization_id,
    ).with_for_update())
    if current is None:
        db.add(SourceCurrent(
            source_id=source_row.id,
            organization_id=project.organization_id,
            version_id=version.id,
        ))
    else:
        current.version_id = version.id
    db.flush()
    return version
