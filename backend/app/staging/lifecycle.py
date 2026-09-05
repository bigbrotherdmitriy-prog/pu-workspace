"""Transaction-owned materialization lifecycle over encrypted staging.

The service never commits, rolls back, enqueues work, or performs provider I/O.
Filesystem effects are recoverable: seal retries use the durable fence and purge
retries rely on idempotent deletion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import BinaryIO, Literal
from uuid import UUID

from pydantic import StrictInt, StrictStr, model_validator
from sqlalchemy import select

from app.core.v54_interfaces import RequestScope
from app.core.v54_permissions import SourceEvidenceError, SyntheticPolicy, deny, utc
from app.core.v54_refs import ObjectRef, StrictDTO, VersionPin, require_same_tenant
from app.models.materialization import Materialization
from app.models.v54_pilot import Evidence, SourceReference, SourceVersion
from app.source_evidence.common import audit, cas
from app.staging.contracts import KekRef, StagingDescriptor, StagingError, StagingStorage
from app.staging.filesystem import new_object_id

_OPAQUE = re.compile(r"^[0-9a-f]{32}$")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_MEDIA = frozenset({
    "text/plain", "text/plain; charset=utf-8", "text/markdown",
    "text/markdown; charset=utf-8", "application/json",
    "application/json; charset=utf-8",
})
_SOURCE_MEDIA = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv", "text/markdown", "text/plain",
})


class StoredDescriptor(StrictDTO):
    object_id: StrictStr
    format_version: StrictInt
    chunk_size: StrictInt
    kek_reference: StrictStr
    kek_version: StrictStr
    wrapped_dek: StrictStr

    @model_validator(mode="after")
    def validate_values(self):
        if (not _OPAQUE.fullmatch(self.object_id) or self.format_version <= 0
                or self.chunk_size <= 0 or not _SAFE.fullmatch(self.kek_reference)
                or not _SAFE.fullmatch(self.kek_version) or not self.wrapped_dek
                or len(self.wrapped_dek) > 255):
            raise ValueError("resource_unavailable")
        return self


class MaterializationManifest(StrictDTO):
    schema_version: Literal["v54.materialization.1"]
    storage: StoredDescriptor
    evidence_pin: VersionPin
    source_ref: ObjectRef
    source_version_pin: VersionPin
    kind: Literal["extracted_text", "quote", "source_object"]
    media_type: StrictStr

    @model_validator(mode="after")
    def validate_binding(self):
        require_same_tenant(
            self.evidence_pin.ref.tenant_id, self.source_ref, self.source_version_pin.ref,
        )
        if (self.evidence_pin.ref.type != "evidence" or self.evidence_pin.value != 1
                or self.source_ref.type != "source"
                or self.source_version_pin.ref.type != "source_version"
                or self.source_version_pin.value != 1
                or self.media_type not in (_SOURCE_MEDIA if self.kind == "source_object" else _MEDIA)):
            raise ValueError("resource_unavailable")
        return self


class PurgeTombstone(StrictDTO):
    schema_version: Literal["v54.materialization.tombstone.1"]
    outcome: Literal["completed", "cancelled", "failed", "dead_letter"] | None = None
    result: dict | None = None


class RetiredMaterializationManifest(StrictDTO):
    schema_version: Literal["v54.materialization.retired.1"]
    storage: StoredDescriptor
    outcome: Literal["completed", "cancelled", "failed", "dead_letter"]
    result: dict | None = None


@dataclass(frozen=True, slots=True)
class LifecycleAuthority:
    """Server-injected policy extension; every materialization capability defaults deny."""
    policy: SyntheticPolicy
    allowed_residencies: frozenset[str] = frozenset()
    allowed_keks: frozenset[KekRef] = frozenset()
    max_retention: timedelta | None = None
    copy_allowed: bool = False
    derive_allowed: bool = False
    retention_owner: bool = False


def _boundary(function):
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SourceEvidenceError:
            raise
        except Exception:
            raise SourceEvidenceError("resource_unavailable") from None
    return call


class MaterializationLifecycle:
    def __init__(self, authority: LifecycleAuthority, storage: StagingStorage, clock):
        if not isinstance(authority, LifecycleAuthority) or not isinstance(storage, StagingStorage):
            deny()
        self.authority, self.storage, self.clock = authority, storage, clock

    def _now(self) -> datetime:
        value = utc(self.clock())
        if value is None or value.tzinfo is None:
            deny()
        return value

    def _require(self, db, scope: RequestScope, operation: str, *, audit_required=False):
        now = self._now()
        self.authority.policy.require(db, scope, operation, now, lock=True)
        if audit_required:
            self.authority.policy.require(db, scope, "audit", now)
        return now

    def _ref(self, scope, identity):
        return ObjectRef(namespace="pu", type="materialization", tenant_id=scope.tenant,
                         id={"kind": "uuid", "value": identity})

    def _pin(self, scope, row):
        return VersionPin(ref=self._ref(scope, row.id), version_kind="record_version",
                          value=row.record_version)

    def _load(self, db, scope, materialization: VersionPin, *, state=None):
        if (not isinstance(materialization, VersionPin)
                or materialization.ref.type != "materialization"
                or materialization.version_kind != "record_version"):
            deny()
        require_same_tenant(scope.tenant, materialization.ref)
        row = db.scalar(select(Materialization).where(
            Materialization.id == materialization.ref.id.value,
            Materialization.organization_id == self.authority.policy.tenant_id,
            Materialization.project_id == self.authority.policy.project_id,
            Materialization.owner_id == int(scope.actor.id.value),
        ).with_for_update())
        if (not row or row.record_version != materialization.value
                or state is not None and row.state not in state):
            deny()
        return row

    @_boundary
    def admit(self, db, *, scope: RequestScope, evidence: VersionPin,
              source_version: VersionPin, residency: str, retention_until: datetime,
              kek: KekRef, allow_copy: bool = False, allow_derive: bool = False,
              parent: VersionPin | None = None, materialization_id: str | None = None,
              object_id: str | None = None) -> VersionPin:
        now = self._require(db, scope, "write", audit_required=True)
        if (not isinstance(evidence, VersionPin) or evidence.ref.type != "evidence"
                or evidence.version_kind != "revision" or evidence.value != 1
                or not isinstance(source_version, VersionPin)
                or source_version.ref.type != "source_version"
                or source_version.version_kind != "revision" or source_version.value != 1):
            deny()
        require_same_tenant(scope.tenant, evidence.ref, source_version.ref)
        retention_until = utc(retention_until)
        maximum = self.authority.max_retention
        if (type(residency) is not str or residency not in self.authority.allowed_residencies
                or maximum is None or maximum <= timedelta(0)
                or retention_until is None or retention_until <= now
                or retention_until > now + maximum or kek not in self.authority.allowed_keks
                or type(allow_copy) is not bool or type(allow_derive) is not bool
                or allow_copy and not self.authority.copy_allowed
                or allow_derive and not self.authority.derive_allowed):
            deny()
        observed = db.scalar(select(SourceVersion).where(
            SourceVersion.id == source_version.ref.id.value,
            SourceVersion.organization_id == self.authority.policy.tenant_id,
            SourceVersion.revision == source_version.value,
        ))
        proof = db.scalar(select(Evidence).where(
            Evidence.id == evidence.ref.id.value,
            Evidence.organization_id == self.authority.policy.tenant_id,
            Evidence.revision == evidence.value,
        ))
        source = db.scalar(select(SourceReference).where(
            SourceReference.id == observed.source_id if observed else False,
            SourceReference.organization_id == self.authority.policy.tenant_id,
            SourceReference.origin_project_id == self.authority.policy.project_id,
        ))
        if (not observed or not proof or not source or proof.source_id != observed.source_id
                or proof.source_version_id != observed.id
                or proof.policy_pins != self.authority.policy.policy_pins()
                or source.policy_pins != self.authority.policy.policy_pins()):
            deny()
        parent_id = None
        if parent is not None:
            parent_row = self._load(db, scope, parent, state={"SEALED", "DERIVED"})
            if (not allow_derive or not parent_row.derive_allowed
                    or parent_row.source_id != proof.source_id
                    or parent_row.source_version_id != proof.source_version_id):
                deny()
            parent_id = parent_row.id
        if materialization_id is not None:
            if type(materialization_id) is not str or str(UUID(materialization_id)) != materialization_id:
                deny()
        if object_id is not None and (type(object_id) is not str or not _OPAQUE.fullmatch(object_id)):
            deny()
        row = Materialization(
            id=materialization_id,
            organization_id=self.authority.policy.tenant_id,
            project_id=self.authority.policy.project_id,
            owner_id=int(scope.actor.id.value), source_id=proof.source_id,
            source_version_id=proof.source_version_id, evidence_id=proof.id,
            parent_id=parent_id, object_id=object_id or new_object_id(), state="ADMITTED",
            kek_reference=kek.reference, kek_version=kek.version,
            residency=residency, retention_until=retention_until,
            copy_allowed=allow_copy, derive_allowed=allow_derive, admitted_at=now,
        )
        db.add(row)
        db.flush()
        result = self._pin(scope, row)
        audit(db, self.authority.policy, scope, result.ref,
              "MATERIALIZATION_ADMITTED", now, result)
        return result

    @_boundary
    def begin_write(self, db, *, scope: RequestScope, materialization: VersionPin,
                    fence: str) -> VersionPin:
        now = self._require(db, scope, "write", audit_required=True)
        if type(fence) is not str or not _OPAQUE.fullmatch(fence):
            deny()
        row = self._load(db, scope, materialization, state={"ADMITTED"})
        cas(db, Materialization, [Materialization.id == row.id,
            Materialization.organization_id == row.organization_id,
            Materialization.state == "ADMITTED"], materialization.value,
            state="WRITING", active_fence=fence, writing_at=now)
        result = VersionPin(ref=materialization.ref, version_kind="record_version",
                            value=materialization.value + 1)
        audit(db, self.authority.policy, scope, result.ref,
              "MATERIALIZATION_WRITING", now, result)
        return result

    @_boundary
    def seal(self, db, *, scope: RequestScope, materialization: VersionPin, fence: str,
             source: BinaryIO, max_bytes: int,
             kind: Literal["extracted_text", "quote", "source_object"],
             media_type: str) -> VersionPin:
        now = self._require(db, scope, "write", audit_required=True)
        row = self._load(db, scope, materialization, state={"WRITING"})
        if (row.active_fence != fence or type(max_bytes) is not int or max_bytes <= 0
                or kind not in {"extracted_text", "quote", "source_object"}
                or media_type not in (_SOURCE_MEDIA if kind == "source_object" else _MEDIA)):
            deny()
        descriptor = self.storage.write(
            row.object_id, source, max_bytes=max_bytes,
            kek=KekRef(row.kek_reference, row.kek_version), fence=fence,
        )
        manifest = MaterializationManifest(
            schema_version="v54.materialization.1",
            storage=StoredDescriptor(
                object_id=descriptor.object_id, format_version=descriptor.format_version,
                chunk_size=descriptor.chunk_size, kek_reference=descriptor.kek.reference,
                kek_version=descriptor.kek.version, wrapped_dek=descriptor.wrapped_dek,
            ),
            evidence_pin=VersionPin(ref=ObjectRef(
                namespace="pu", type="evidence", tenant_id=scope.tenant,
                id={"kind": "uuid", "value": row.evidence_id}),
                version_kind="revision", value=1),
            source_ref=ObjectRef(namespace="pu", type="source", tenant_id=scope.tenant,
                                 id={"kind": "uuid", "value": row.source_id}),
            source_version_pin=VersionPin(ref=ObjectRef(
                namespace="pu", type="source_version", tenant_id=scope.tenant,
                id={"kind": "uuid", "value": row.source_version_id}),
                version_kind="revision", value=1),
            kind=kind, media_type=media_type,
        )
        cas(db, Materialization, [Materialization.id == row.id,
            Materialization.organization_id == row.organization_id,
            Materialization.state == "WRITING", Materialization.active_fence == fence],
            materialization.value, state="SEALED", format_version=descriptor.format_version,
            chunk_size=descriptor.chunk_size, wrapped_dek=descriptor.wrapped_dek,
            manifest=manifest.model_dump(mode="json"), sealed_at=now)
        result = VersionPin(ref=materialization.ref, version_kind="record_version",
                            value=materialization.value + 1)
        audit(db, self.authority.policy, scope, result.ref,
              "MATERIALIZATION_SEALED", now, result)
        return result

    @_boundary
    def derive(self, db, *, scope: RequestScope, materialization: VersionPin) -> VersionPin:
        now = self._require(db, scope, "write", audit_required=True)
        row = self._load(db, scope, materialization, state={"SEALED"})
        if not row.derive_allowed or not self.authority.derive_allowed or utc(row.retention_until) <= now:
            deny()
        manifest = MaterializationManifest.model_validate(row.manifest)
        evidence = db.scalar(select(Evidence).where(
            Evidence.id == row.evidence_id,
            Evidence.organization_id == row.organization_id,
            Evidence.source_id == row.source_id,
            Evidence.source_version_id == row.source_version_id,
        ).with_for_update())
        if not evidence or evidence.representation_ref is not None:
            deny()
        cas(db, Materialization, [Materialization.id == row.id,
            Materialization.organization_id == row.organization_id,
            Materialization.state == "SEALED"], materialization.value,
            state="DERIVED", derived_at=now, active_fence=None)
        evidence = db.scalar(select(Evidence).where(
            Evidence.id == row.evidence_id,
            Evidence.organization_id == row.organization_id,
            Evidence.source_id == row.source_id,
            Evidence.source_version_id == row.source_version_id,
        ).with_for_update())
        if not evidence or evidence.representation_ref is not None:
            deny()
        evidence.representation_ref = {
            "schema_version": "v54.fragment.1", "representation_id": row.id,
            "handle": row.object_id,
            "evidence_pin": manifest.evidence_pin.model_dump(mode="json"),
            "source_ref": manifest.source_ref.model_dump(mode="json"),
            "source_version_pin": manifest.source_version_pin.model_dump(mode="json"),
            "kind": manifest.kind, "media_type": manifest.media_type,
            "retention_state": "active", "expires_at": utc(row.retention_until).isoformat(),
        }
        db.flush()
        result = VersionPin(ref=materialization.ref, version_kind="record_version",
                            value=materialization.value + 1)
        audit(db, self.authority.policy, scope, result.ref,
              "MATERIALIZATION_DERIVED", now, result)
        return result

    def _storage_descriptor(self, row) -> StagingDescriptor:
        manifest = (RetiredMaterializationManifest.model_validate(row.manifest)
                    if row.state == "EXPIRED"
                    else MaterializationManifest.model_validate(row.manifest))
        value = manifest.storage
        if (value.object_id != row.object_id or value.format_version != row.format_version
                or value.chunk_size != row.chunk_size or value.kek_reference != row.kek_reference
                or value.kek_version != row.kek_version or value.wrapped_dek != row.wrapped_dek):
            deny()
        return StagingDescriptor(
            object_id=value.object_id, format_version=value.format_version,
            chunk_size=value.chunk_size, kek=KekRef(value.kek_reference, value.kek_version),
            wrapped_dek=value.wrapped_dek,
        )

    @_boundary
    def authorize_read(self, db, *, scope: RequestScope,
                       materialization: VersionPin, max_bytes: int,
                       for_copy: bool = False) -> StagingDescriptor:
        """Return a descriptor only; the transaction owner commits before I/O."""
        now = self._require(db, scope, "write" if for_copy else "fragment")
        row = self._load(db, scope, materialization, state={"DERIVED"})
        if (utc(row.retention_until) <= now or type(max_bytes) is not int or max_bytes <= 0
                or type(for_copy) is not bool
                or for_copy and (not row.copy_allowed or not self.authority.copy_allowed)):
            deny()
        return self._storage_descriptor(row)

    @_boundary
    def read(self, db, *, scope: RequestScope, materialization: VersionPin,
             max_bytes: int, for_copy: bool = False) -> bytes:
        descriptor = self.authorize_read(
            db, scope=scope, materialization=materialization,
            max_bytes=max_bytes, for_copy=for_copy,
        )
        return b"".join(self.storage.read_chunks(descriptor, max_bytes=max_bytes))

    @_boundary
    def retire(self, db, *, scope: RequestScope, materialization: VersionPin,
               outcome: Literal["completed", "cancelled"],
               result: dict | None = None) -> VersionPin:
        """Authorize early cleanup after a durable terminal processing outcome."""
        now = self._require(db, scope, "write", audit_required=True)
        if not self.authority.retention_owner or outcome not in {"completed", "cancelled"}:
            deny()
        row = self._load(db, scope, materialization, state={"DERIVED"})
        storage = MaterializationManifest.model_validate(row.manifest).storage
        retired = RetiredMaterializationManifest(
            schema_version="v54.materialization.retired.1", storage=storage,
            outcome=outcome, result=result,
        )
        cas(db, Materialization, [Materialization.id == row.id,
            Materialization.organization_id == row.organization_id,
            Materialization.state == "DERIVED"], materialization.value,
            state="EXPIRED", expired_at=now, active_fence=None,
            manifest=retired.model_dump(mode="json"))
        result_pin = VersionPin(ref=materialization.ref, version_kind="record_version",
                                value=materialization.value + 1)
        audit(db, self.authority.policy, scope, result_pin.ref,
              "MATERIALIZATION_EXPIRED", now, result_pin)
        return result_pin

    @_boundary
    def expire(self, db, *, scope: RequestScope, materialization: VersionPin) -> VersionPin:
        now = self._require(db, scope, "write", audit_required=True)
        if not self.authority.retention_owner:
            deny()
        row = self._load(db, scope, materialization,
                         state={"ADMITTED", "WRITING", "SEALED", "DERIVED"})
        if utc(row.retention_until) > now:
            deny()
        if row.state == "WRITING" and row.active_fence:
            self.storage.cleanup_partials(row.object_id, eligible_fences={row.active_fence},
                                          active_fences=set())
            self.storage.delete(row.object_id)
        expired_at = now
        cas(db, Materialization, [Materialization.id == row.id,
            Materialization.organization_id == row.organization_id,
            Materialization.state == row.state], materialization.value,
            state="EXPIRED", expired_at=expired_at, active_fence=None)
        result = VersionPin(ref=materialization.ref, version_kind="record_version",
                            value=materialization.value + 1)
        audit(db, self.authority.policy, scope, result.ref,
              "MATERIALIZATION_EXPIRED", now, result)
        return result

    @_boundary
    def purge(self, db, *, scope: RequestScope, materialization: VersionPin) -> VersionPin:
        now = self._require(db, scope, "write", audit_required=True)
        if not self.authority.retention_owner:
            deny()
        row = self._load(db, scope, materialization, state={"EXPIRED"})
        self.storage.delete(row.object_id)
        retired = None
        try:
            retired = RetiredMaterializationManifest.model_validate(row.manifest)
        except Exception:
            pass
        cas(db, Materialization, [Materialization.id == row.id,
            Materialization.organization_id == row.organization_id,
            Materialization.state == "EXPIRED"], materialization.value,
            state="PURGED", manifest=PurgeTombstone(
                schema_version="v54.materialization.tombstone.1",
                outcome=retired.outcome if retired else None,
                result=retired.result if retired else None,
            ).model_dump(mode="json", exclude_none=True),
            wrapped_dek=None, format_version=None, chunk_size=None, active_fence=None,
            purged_at=now)
        result = VersionPin(ref=materialization.ref, version_kind="record_version",
                            value=materialization.value + 1)
        audit(db, self.authority.policy, scope, result.ref,
              "MATERIALIZATION_PURGED", now, result)
        return result
