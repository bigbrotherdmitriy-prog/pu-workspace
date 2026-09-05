"""Read-only authority for the existing exact staged local-upload lineage.

This does not read bytes, create identities, synthesize grants or infer a local
source from a client provider label. All identities are persisted A05 bindings.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictInt, StrictStr
from sqlalchemy import inspect, select
from sqlalchemy.orm.attributes import NO_VALUE

from app.core.v54_authority import AuthorityResolver
from app.core.v54_interfaces import RequestScope
from app.core.v54_permissions import SourceEvidenceError, deny, utc, utcnow
from app.core.v54_refs import ObjectRef, StrictDTO, VersionPin, require_same_tenant
from app.models.materialization import Materialization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import ConnectionIdentity, Evidence, SourceCurrent, SourceReference, SourceVersion
from app.staging.lifecycle import MaterializationManifest, PurgeTombstone


class LocalUploadObservation(StrictDTO):
    kind: Literal["local_upload"]
    staging_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    display_name: StrictStr = Field(min_length=1, max_length=255)
    media_type: StrictStr = Field(min_length=1, max_length=255)
    size: StrictInt = Field(ge=0)
    fence: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")


class _OriginalRepresentation(StrictDTO):
    schema_version: Literal["v54.fragment.1"]
    representation_id: StrictStr
    handle: StrictStr
    evidence_pin: VersionPin
    source_ref: ObjectRef
    source_version_pin: VersionPin
    kind: Literal["source_object"]
    media_type: StrictStr
    retention_state: Literal["active"]
    expires_at: AwareDatetime


@dataclass(frozen=True, slots=True)
class LocalSourceBinding:
    source_ref: ObjectRef
    source_version_pin: VersionPin
    source_record_version: int
    original_materialization_id: str
    owner_id: int
    checksum_sha256: str
    size: int
    media_type: str
    original_state: str
    retention_until: datetime
    valid_until: datetime
    authority_epoch: int
    binding_epoch: int


def _deny_pending_security_changes(db, *, tenant, project, owner, source_id, version_id):
    """Do not let refresh replace caller-owned pending authorization state.

    Inspect loaded values and their previous values without lazy loading. Unknown
    scope is conservative denial. Child evidence/materializations are not the
    original authority chain and may be pending in a publishing transaction.
    """
    from app.staging.local_upload import _stable_uuid

    def may_match(state, field, expected):
        values = []
        if field in state.dict:
            values.append(state.dict[field])
        if state.identity is not None and field in state.committed_state:
            values.append(state.committed_state[field])
        for index, column in enumerate(state.mapper.primary_key):
            if column.key == field and state.identity is not None:
                values.append(state.identity[index])
        return not values or any(value is NO_VALUE or value == expected for value in values)

    for row in set(db.new) | set(db.dirty) | set(db.deleted):
        state = inspect(row)
        match = lambda field, value: may_match(state, field, value)
        relevant = (
            isinstance(row, Project) and match("id", project)
            or isinstance(row, User) and match("id", owner)
            or isinstance(row, ProjectMember) and match("project_id", project) and match("user_id", owner)
            or isinstance(row, AuthorityState) and match("organization_id", tenant)
                and match("project_id", project) and match("principal_kind", "user") and match("principal_id", str(owner))
            or isinstance(row, SourceReference) and match("id", source_id)
            or isinstance(row, SourceVersion) and match("id", version_id)
            or isinstance(row, SourceCurrent) and match("source_id", source_id)
            or isinstance(row, ConnectionIdentity) and match("id", _stable_uuid("identity", f"{tenant}:{owner}"))
            or isinstance(row, Evidence) and match("id", _stable_uuid("evidence", source_id))
            or isinstance(row, Materialization) and match("source_id", source_id) and match("parent_id", None)
        )
        if relevant:
            deny()


def require_local_upload_source(
    db, *, scope: RequestScope, source_ref: ObjectRef,
    source_version_pin: VersionPin, operation: str = "metadata", lock: bool = True,
    allow_purged_original: bool = False, clock=utcnow,
) -> LocalSourceBinding:
    """Authorize metadata for one current original, never its plaintext.

    Default requires a live DERIVED original. Only a caller independently
    authorizing a retained child representation may opt into a completed PURGED
    original; this does not assert original-byte availability.
    """
    try:
        return _require(db, scope=scope, source_ref=source_ref,
                        source_version_pin=source_version_pin, operation=operation,
                        lock=lock, allow_purged_original=allow_purged_original, clock=clock)
    except SourceEvidenceError:
        raise
    except Exception:
        raise SourceEvidenceError("resource_unavailable") from None


def _require(db, *, scope, source_ref, source_version_pin, operation, lock, allow_purged_original, clock):
    from app.local_upload_staging import DEFAULT_ALLOWED_MIME_TYPES, safe_display_name
    from app.staging.local_upload import _stable_uuid

    now = utc(clock())
    if (not db.in_transaction() or not isinstance(scope, RequestScope)
            or not isinstance(source_ref, ObjectRef) or source_ref.type != "source"
            or not isinstance(source_version_pin, VersionPin)
            or source_version_pin.ref.type != "source_version"
            or source_version_pin.version_kind != "revision" or source_version_pin.value != 1
            or operation not in {"metadata", "fragment", "write"}
            or type(lock) is not bool or type(allow_purged_original) is not bool
            or now is None or now.tzinfo is None):
        deny()
    require_same_tenant(scope.tenant, scope.actor, scope.project, source_ref, source_version_pin.ref)
    tenant, project, owner = int(scope.tenant.value), int(scope.project.id.value), int(scope.actor.id.value)
    _deny_pending_security_changes(db, tenant=tenant, project=project, owner=owner,
                                  source_id=source_ref.id.value, version_id=source_version_pin.ref.id.value)

    def load(model, *conditions):
        query = select(model).where(*conditions).execution_options(populate_existing=True)
        return db.scalar(query.with_for_update() if lock else query)

    with db.no_autoflush:
        mandate = AuthorityResolver(clock=clock).require(db, scope, operation, now, lock=lock)
        source = load(SourceReference, SourceReference.id == source_ref.id.value,
                      SourceReference.organization_id == tenant, SourceReference.origin_project_id == project)
        version = load(SourceVersion, SourceVersion.id == source_version_pin.ref.id.value,
                       SourceVersion.organization_id == tenant, SourceVersion.source_id == source_ref.id.value)
        if source is None or version is None:
            deny()
        metadata = LocalUploadObservation.model_validate(version.locator_at_observation)
        current = load(SourceCurrent, SourceCurrent.source_id == source.id, SourceCurrent.organization_id == tenant)
        original = load(Materialization, Materialization.id == str(UUID(hex=metadata.staging_id)),
                        Materialization.organization_id == tenant, Materialization.project_id == project,
                        Materialization.owner_id == owner)
        identity = load(ConnectionIdentity, ConnectionIdentity.id == source.identity_id,
                        ConnectionIdentity.organization_id == tenant)
        proof = load(Evidence, Evidence.id == original.evidence_id,
                     Evidence.organization_id == tenant) if original else None
        if not all((current, original, identity, proof)):
            deny()
        expected_source = _stable_uuid("source", f"{tenant}:{owner}:{project}:{source.external_id}")
        if (source.namespace != "local-upload" or source.object_kind != "file" or source.parent_source_id is not None
                or source.external_id_kind != "stable_id" or len(source.external_id) != 64
                or any(c not in "0123456789abcdef" for c in source.external_id)
                or source.id != expected_source or version.id != _stable_uuid("version", source.id)
                or metadata.staging_id != source.external_id[:32]
                or source.canonical_locator != {"kind": "opaque_id", "value": metadata.staging_id, "normalization_version": "1"}
                or current.version_id != version.id or version.revision != 1
                or version.consistency != "digest_observed" or version.observation_key != source.external_id
                or original.parent_id is not None or original.source_id != source.id or original.source_version_id != version.id
                or original.copy_allowed or not original.derive_allowed
                or proof.id != _stable_uuid("evidence", source.id) or proof.source_id != source.id
                or proof.source_version_id != version.id or proof.revision != 1
                or proof.locator != {"kind": "whole_object", "reason_code": "local_upload"}
                or proof.extractor != {"name": "local_upload", "version": "1"}
                or identity.id != _stable_uuid("identity", f"{tenant}:{owner}")
                or identity.provider != "local_upload" or identity.state != "verified"
                or identity.account_key != hashlib.sha256(f"{tenant}:{owner}".encode("ascii")).hexdigest()
                or identity.credential_id is not None or identity.credential_generation != 1 or identity.binding_epoch != 1
                or identity.record_version < 1 or utc(identity.verified_at) is None or utc(identity.verified_at) > now
                or source.availability != "available" or source.freshness != "fresh" or source.record_version < 1
                or utc(version.observed_at) is None or utc(version.observed_at) > now
                or utc(source.last_checked_at) is None or utc(source.last_checked_at) > now
                or metadata.media_type not in DEFAULT_ALLOWED_MIME_TYPES
                or safe_display_name(metadata.display_name) != metadata.display_name):
            deny()
        if (not isinstance(version.integrity, list) or len(version.integrity) != 2
                or not isinstance(source.policy_pins, dict) or set(source.policy_pins) != {"access", "retention", "residency"}
                or proof.policy_pins != source.policy_pins or not isinstance(source.residency, dict)
                or source.residency.get("source_location") != original.residency):
            deny()
        for entry, algorithm in zip(version.integrity, ("sha256", "request-fingerprint-sha256")):
            if (not isinstance(entry, dict) or set(entry) != {"algorithm", "value"}
                    or entry["algorithm"] != algorithm or not isinstance(entry["value"], str)
                    or len(entry["value"]) != 64 or any(c not in "0123456789abcdef" for c in entry["value"])):
                deny()
        if version.provider_revision != version.integrity[1]["value"]:
            deny()
        for raw in source.policy_pins.values():
            policy = VersionPin.model_validate(raw)
            require_same_tenant(scope.tenant, policy.ref)
            if policy.ref.type != "policy" or policy.version_kind != "revision" or type(policy.value) is not int or policy.value < 1:
                deny()
        retention, checked_until = utc(original.retention_until), utc(source.next_check_at)
        if retention is None or checked_until is None or min(retention, checked_until, mandate.valid_until) <= now:
            deny()
        if original.state == "DERIVED":
            manifest = MaterializationManifest.model_validate(original.manifest)
            representation = _OriginalRepresentation.model_validate(proof.representation_ref)
            proof_pin = VersionPin(ref=ObjectRef(namespace="pu", type="evidence", tenant_id=scope.tenant,
                                                id={"kind": "uuid", "value": proof.id}),
                                   version_kind="revision", value=1)
            if (manifest.kind != "source_object" or manifest.media_type != metadata.media_type
                    or manifest.source_ref != source_ref or manifest.source_version_pin != source_version_pin
                    or manifest.evidence_pin != proof_pin or manifest.evidence_pin.version_kind != "revision"
                    or manifest.storage.object_id != original.object_id
                    or manifest.storage.kek_reference != original.kek_reference or manifest.storage.kek_version != original.kek_version
                    or manifest.storage.wrapped_dek != original.wrapped_dek
                    or manifest.storage.format_version != original.format_version or manifest.storage.chunk_size != original.chunk_size
                    or representation.representation_id != original.id or representation.handle != original.object_id
                    or representation.evidence_pin != proof_pin or representation.source_ref != source_ref
                    or representation.source_version_pin != source_version_pin
                    or representation.media_type != metadata.media_type or utc(representation.expires_at) != retention):
                deny()
        elif (not allow_purged_original or original.state != "PURGED"
              or PurgeTombstone.model_validate(original.manifest).outcome != "completed"):
            deny()
        return LocalSourceBinding(source_ref, source_version_pin, source.record_version, original.id, owner,
                                  version.integrity[0]["value"], metadata.size, metadata.media_type, original.state,
                                  retention, min(retention, checked_until, mandate.valid_until),
                                  mandate.authority_epoch, identity.binding_epoch)
