"""Local-upload composition over the authoritative a05 source/materialization model.

The adapter owns no commits and no queue.  It converts the opaque local-upload
request into exact SourceReference/SourceVersion/Evidence/Materialization rows,
then delegates every representation transition to MaterializationLifecycle.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any, Callable, Mapping
from uuid import UUID, uuid5

from sqlalchemy import func, select

from app.core.v54_interfaces import RequestScope
from app.core.v54_permissions import SourceEvidenceError, object_ref, utc
from app.core.v54_refs import ObjectRef, TaggedId, VersionPin
from app.jobs.queue import utcnow as queue_now
from app.local_upload_staging import (
    CleanupDecision, FinalizedUpload, LocalUploadConflict, MaterializedUpload,
    UploadReservation, UploadScope,
)
from app.models.job import BackgroundJob
from app.models.audit_log import AuditLog
from app.models.materialization import Materialization
from app.models.project import Project
from app.models.v54_pilot import (
    AuditExtension, ConnectionIdentity, Evidence, SourceCurrent, SourceReference,
    SourceVersion,
)
from app.source_evidence.common import audit, cas
from app.staging.contracts import KekRef, StagingDescriptor, StagingStorage
from app.staging.lifecycle import (
    LifecycleAuthority, MaterializationLifecycle, MaterializationManifest,
    PurgeTombstone, RetiredMaterializationManifest,
)

_NAMESPACE = UUID("a4d8f512-6b68-4dd6-9df4-7384bd62292d")
_SERVICE_PRINCIPAL = re.compile(r"^[a-z][a-z0-9.-]{2,99}$")


@dataclass(frozen=True, slots=True)
class LocalUploadRetentionAuthority:
    """Explicit server capability; it never derives authority from a user row."""

    service_principal: str
    scopes: frozenset[tuple[int, int]]
    allowed_residencies: frozenset[str]
    allowed_keks: frozenset[KekRef]

    def __post_init__(self) -> None:
        if (
            not _SERVICE_PRINCIPAL.fullmatch(self.service_principal)
            or not self.scopes
            or any(type(tenant) is not int or tenant <= 0 or type(project) is not int
                   or project <= 0 for tenant, project in self.scopes)
            or not self.allowed_residencies or not self.allowed_keks
        ):
            raise ValueError("resource_unavailable")

    def require(self, db: Any, row: Materialization) -> None:
        project = db.scalar(select(Project).where(
            Project.id == row.project_id,
            Project.organization_id == row.organization_id,
        ).with_for_update())
        if (
            project is None
            or (row.organization_id, row.project_id) not in self.scopes
            or row.residency not in self.allowed_residencies
            or KekRef(row.kek_reference, row.kek_version) not in self.allowed_keks
        ):
            raise SourceEvidenceError("resource_unavailable")


def _stable_uuid(label: str, value: str) -> str:
    return str(uuid5(_NAMESPACE, f"{label}\x00{value}"))


def _pin(scope: RequestScope, kind: str, identity: str, value: int = 1) -> VersionPin:
    return VersionPin(
        ref=object_ref(scope, kind, identity),
        version_kind="record_version" if kind == "source" else "revision",
        value=value,
    )


def _descriptor(storage: Mapping[str, Any]) -> StagingDescriptor:
    return StagingDescriptor(
        object_id=storage["object_id"], format_version=storage["format_version"],
        chunk_size=storage["chunk_size"],
        kek=KekRef(storage["kek_reference"], storage["kek_version"]),
        wrapped_dek=storage["wrapped_dek"],
    )


class A05LocalUploadLifecycle:
    """Concrete backend accepted by ``LocalUploadLifecycleAdapter``.

    ``authority_factory`` is server composition, never request input.  It must
    return an authority for the exact owner/project carried by ``UploadScope``.
    """

    def __init__(
        self, *, storage: StagingStorage,
        authority_factory: Callable[[Any, UploadScope], LifecycleAuthority],
        clock: Callable[[], datetime], residency: str, kek: KekRef,
        max_file_bytes: int,
        retention_authority: LocalUploadRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(storage, StagingStorage) or not callable(authority_factory)
                or not callable(clock) or type(residency) is not str or not residency
                or not isinstance(kek, KekRef) or type(max_file_bytes) is not int
                or max_file_bytes <= 0):
            raise ValueError("resource_unavailable")
        self.storage = storage
        self.authority_factory = authority_factory
        self.clock = clock
        self.residency = residency
        self.kek = kek
        self.max_file_bytes = max_file_bytes
        if retention_authority is not None and not isinstance(
            retention_authority, LocalUploadRetentionAuthority,
        ):
            raise ValueError("resource_unavailable")
        self.retention_authority = retention_authority

    def _service(self, db: Any, upload_scope: UploadScope) -> tuple[MaterializationLifecycle, RequestScope]:
        authority = self.authority_factory(db, upload_scope)
        if (not isinstance(authority, LifecycleAuthority)
                or authority.policy.project_id != upload_scope.project_id):
            raise SourceEvidenceError("resource_unavailable")
        tenant = authority.policy.tenant_id
        scope = RequestScope(
            actor={"namespace": "pu", "type": "user",
                   "tenant_id": {"kind": "int", "value": str(tenant)},
                   "id": {"kind": "int", "value": str(upload_scope.owner_id)}},
            tenant={"kind": "int", "value": str(tenant)},
            project={"namespace": "pu", "type": "project",
                     "tenant_id": {"kind": "int", "value": str(tenant)},
                     "id": {"kind": "int", "value": str(upload_scope.project_id)}},
            correlation_id=_stable_uuid(
                "correlation", f"{tenant}:{upload_scope.owner_id}:{upload_scope.project_id}",
            ),
        )
        return MaterializationLifecycle(authority, self.storage, self.clock), scope

    @staticmethod
    def _materialization_id(staging_id: str) -> str:
        if type(staging_id) is not str or len(staging_id) != 32:
            raise SourceEvidenceError("resource_unavailable")
        try:
            return str(UUID(hex=staging_id))
        except (ValueError, AttributeError):
            raise SourceEvidenceError("resource_unavailable") from None

    @staticmethod
    def _staging_id(materialization_id: str) -> str:
        return UUID(materialization_id).hex

    def _job(self, db: Any, row: Materialization, job_id: int | None = None) -> BackgroundJob | None:
        source = db.get(SourceReference, row.source_id)
        if not source:
            raise SourceEvidenceError("resource_unavailable")
        conditions = [
            BackgroundJob.kind == "local_upload.process",
            BackgroundJob.idempotency_key == f"local-upload:{source.external_id}",
        ]
        if job_id is not None:
            conditions.append(BackgroundJob.id == job_id)
        job = db.scalar(select(BackgroundJob).where(*conditions).with_for_update())
        if job is not None and job.payload != {"staging_id": self._staging_id(row.id)}:
            raise SourceEvidenceError("resource_unavailable")
        return job

    @staticmethod
    def _require_live_claim(job: BackgroundJob | None, claim: tuple[Any, ...]) -> None:
        if (job is None or not isinstance(claim, tuple) or len(claim) != 4
                or claim[0] != job.id or claim[1] != job.worker_id
                or claim[2] != job.attempts or job.status != "running"
                or job.lease_expires_at is None or utc(job.lease_expires_at) <= queue_now()
                or claim[3] is None or utc(claim[3]) != utc(job.locked_at)):
            raise SourceEvidenceError("resource_unavailable")

    def _bound(
        self, db: Any, upload_scope: UploadScope, staging_id: str,
    ) -> tuple[MaterializationLifecycle, RequestScope, Materialization, SourceVersion]:
        service, scope = self._service(db, upload_scope)
        row = db.scalar(select(Materialization).where(
            Materialization.id == self._materialization_id(staging_id),
            Materialization.organization_id == service.authority.policy.tenant_id,
            Materialization.project_id == upload_scope.project_id,
            Materialization.owner_id == upload_scope.owner_id,
        ).with_for_update())
        version = db.get(SourceVersion, row.source_version_id) if row else None
        source = db.get(SourceReference, row.source_id) if row else None
        current = db.get(SourceCurrent, row.source_id) if row else None
        evidence = db.get(Evidence, row.evidence_id) if row else None
        identity_id = _stable_uuid(
            "identity",
            f"{service.authority.policy.tenant_id}:{upload_scope.owner_id}",
        )
        identity = db.get(ConnectionIdentity, identity_id)
        if (not row or not version or not source or not current
                or not evidence or not identity
                or version.organization_id != row.organization_id
                or version.source_id != row.source_id or current.version_id != version.id
                or source.origin_project_id != row.project_id
                or source.identity_id != identity.id or source.namespace != "local-upload"
                or source.external_id != version.observation_key
                or source.canonical_locator != {
                    "kind": "opaque_id", "value": staging_id,
                    "normalization_version": "1",
                }
                or source.policy_pins != service.authority.policy.policy_pins()
                or source.availability != "available" or source.freshness != "fresh"
                or identity.organization_id != row.organization_id
                or identity.provider != "local_upload" or identity.state != "verified"
                or identity.binding_epoch != 1
                or evidence.source_id != row.source_id
                or evidence.source_version_id != row.source_version_id
                or evidence.policy_pins != service.authority.policy.policy_pins()
                or version.locator_at_observation.get("staging_id") != staging_id):
            raise SourceEvidenceError("resource_unavailable")
        if row.state == "DERIVED":
            manifest = MaterializationManifest.model_validate(row.manifest)
            representation = evidence.representation_ref
            if (manifest.evidence_pin.ref.id.value != row.evidence_id
                    or manifest.source_ref.id.value != row.source_id
                    or manifest.source_version_pin.ref.id.value != row.source_version_id
                    or not isinstance(representation, dict)
                    or representation.get("representation_id") != row.id
                    or representation.get("handle") != row.object_id):
                raise SourceEvidenceError("resource_unavailable")
        return service, scope, row, version

    def reserve(
        self, db: Any, *, scope: UploadScope, request_key: str, object_id: str,
        fence: str, fingerprint: str, display_name: str, mime_type: str,
        checksum: str, size: int, expires_at: datetime,
    ) -> UploadReservation:
        service, request_scope = self._service(db, scope)
        now = utc(self.clock())
        policy = service.authority.policy
        policy.require(db, request_scope, "write", now, lock=True)
        policy.require(db, request_scope, "observe", now, lock=True)
        policy.require(db, request_scope, "audit", now)
        if (len(request_key) != 64 or len(fingerprint) != 64 or len(checksum) != 64
                or any(c not in "0123456789abcdef" for c in request_key + fingerprint + checksum)
                or type(size) is not int or not 0 <= size <= self.max_file_bytes):
            raise SourceEvidenceError("resource_unavailable")

        identity_id = _stable_uuid("identity", f"{policy.tenant_id}:{scope.owner_id}")
        source_id = _stable_uuid("source", f"{policy.tenant_id}:{scope.owner_id}:{scope.project_id}:{request_key}")
        version_id = _stable_uuid("version", source_id)
        evidence_id = _stable_uuid("evidence", source_id)
        materialization_id = str(UUID(hex=request_key[:32]))
        staging_id = UUID(materialization_id).hex
        identity = db.get(ConnectionIdentity, identity_id)
        expected_account = hashlib.sha256(
            f"{policy.tenant_id}:{scope.owner_id}".encode("ascii"),
        ).hexdigest()
        if identity is None:
            identity = ConnectionIdentity(
                id=identity_id, organization_id=policy.tenant_id,
                provider="local_upload",
                account_key=expected_account,
                state="verified", binding_epoch=1, record_version=1,
                credential_generation=1, verified_at=now,
            )
            db.add(identity)
            db.flush()
        elif (identity.organization_id != policy.tenant_id
              or identity.provider != "local_upload" or identity.state != "verified"
              or identity.account_key != expected_account or identity.binding_epoch != 1):
            raise SourceEvidenceError("resource_unavailable")

        source = db.get(SourceReference, source_id)
        metadata = {
            "kind": "local_upload", "staging_id": staging_id,
            "display_name": display_name, "media_type": mime_type,
            "size": size, "fence": fence,
        }
        integrity = [
            {"algorithm": "sha256", "value": checksum},
            {"algorithm": "request-fingerprint-sha256", "value": fingerprint},
        ]
        if source is None:
            source = SourceReference(
                id=source_id, organization_id=policy.tenant_id,
                origin_project_id=scope.project_id, identity_id=identity.id,
                namespace="local-upload", external_id=request_key,
                external_id_kind="stable_id", incarnation=1, object_kind="file",
                canonical_locator={"kind": "opaque_id", "value": staging_id,
                                   "normalization_version": "1"},
                freshness="fresh", sync_state="current", availability="available",
                last_seen_at=now, last_checked_at=now, next_check_at=expires_at,
                policy_pins=policy.policy_pins(),
                residency={"source_location": self.residency,
                           "assurance": "synthetic_local_upload"},
            )
            db.add(source)
            db.flush()
            version = SourceVersion(
                id=version_id, organization_id=policy.tenant_id, source_id=source.id,
                observation_key=request_key, provider_revision=fingerprint,
                consistency="digest_observed", locator_at_observation=metadata,
                integrity=integrity, observed_at=now,
            )
            db.add(version)
            db.flush()
            db.add(SourceCurrent(
                source_id=source.id, organization_id=policy.tenant_id,
                version_id=version.id,
            ))
            evidence = Evidence(
                id=evidence_id, organization_id=policy.tenant_id,
                source_id=source.id, source_version_id=version.id,
                locator={"kind": "whole_object", "reason_code": "local_upload"},
                extractor={"name": "local_upload", "version": "1"},
                confidence=None, confidence_kind="unknown", extracted_at=now,
                policy_pins=policy.policy_pins(),
            )
            db.add(evidence)
            db.flush()
            audit(db, policy, request_scope, object_ref(request_scope, "source", source.id),
                  "SOURCE_OBSERVED", now, _pin(request_scope, "source", source.id))
            audit(db, policy, request_scope, object_ref(request_scope, "source_version", version.id),
                  "SOURCE_OBSERVED", now, _pin(request_scope, "source_version", version.id))
            audit(db, policy, request_scope, object_ref(request_scope, "evidence", evidence.id),
                  "SOURCE_OBSERVED", now, _pin(request_scope, "evidence", evidence.id))
            admitted = service.admit(
                db, scope=request_scope, evidence=_pin(request_scope, "evidence", evidence.id),
                source_version=_pin(request_scope, "source_version", version.id),
                residency=self.residency, retention_until=expires_at, kek=self.kek,
                allow_derive=True, materialization_id=materialization_id,
                object_id=object_id,
            )
            service.begin_write(
                db, scope=request_scope, materialization=admitted, fence=fence,
            )
        else:
            version = db.get(SourceVersion, version_id)
            row = db.get(Materialization, materialization_id)
            persisted = dict(version.locator_at_observation) if version else {}
            if (not version or not row or source.organization_id != policy.tenant_id
                    or source.origin_project_id != scope.project_id
                    or source.identity_id != identity.id or source.external_id != request_key
                    or source.policy_pins != policy.policy_pins()
                    or persisted.get("kind") != "local_upload"
                    or persisted.get("staging_id") != staging_id
                    or not isinstance(persisted.get("display_name"), str)
                    or not isinstance(persisted.get("media_type"), str)
                    or type(persisted.get("size")) is not int
                    or not isinstance(version.integrity, list) or len(version.integrity) != 2
                    or row.owner_id != scope.owner_id or row.project_id != scope.project_id
                    or row.source_version_id != version.id):
                raise LocalUploadConflict("idempotency_conflict")

        row = db.get(Materialization, materialization_id, populate_existing=True)
        descriptor = None
        if row.state == "DERIVED":
            descriptor = _descriptor(MaterializationManifest.model_validate(row.manifest).storage.model_dump())
        job = self._job(db, row)
        return UploadReservation(
            staging_id=staging_id, object_id=row.object_id,
            fence=version.locator_at_observation["fence"],
            fingerprint=fingerprint, state=row.state.lower(), descriptor=descriptor,
            job_id=job.id if job else None,
        )

    def publish(
        self, db: Any, *, scope: UploadScope, staging_id: str,
        descriptor: StagingDescriptor, checksum: str, size: int,
    ) -> UploadReservation:
        service, request_scope, row, version = self._bound(db, scope, staging_id)
        metadata = dict(version.locator_at_observation)
        if (metadata.get("size") != size or version.integrity[0] != {
                "algorithm": "sha256", "value": checksum}
                or descriptor.object_id != row.object_id
                or descriptor.kek != self.kek):
            raise SourceEvidenceError("resource_unavailable")
        if row.state == "WRITING":
            writing = service._pin(request_scope, row)
            sealed = service.seal(
                db, scope=request_scope, materialization=writing,
                fence=metadata["fence"], source=BytesIO(b""),
                max_bytes=self.max_file_bytes, kind="source_object",
                media_type=metadata["media_type"],
            )
            service.derive(db, scope=request_scope, materialization=sealed)
            row = db.get(Materialization, row.id, populate_existing=True)
        if row.state != "DERIVED":
            raise SourceEvidenceError("resource_unavailable")
        actual = _descriptor(MaterializationManifest.model_validate(row.manifest).storage.model_dump())
        if actual != descriptor:
            raise SourceEvidenceError("resource_unavailable")
        job = self._job(db, row)
        return UploadReservation(
            staging_id=staging_id, object_id=row.object_id, fence=metadata["fence"],
            fingerprint=version.provider_revision, state="published", descriptor=actual,
            job_id=job.id if job else None,
        )

    def bind_job(
        self, db: Any, *, scope: UploadScope, staging_id: str, job_id: int,
    ) -> UploadReservation:
        _, _, row, version = self._bound(db, scope, staging_id)
        if row.state != "DERIVED":
            raise SourceEvidenceError("resource_unavailable")
        job = self._job(db, row, job_id)
        if job is None or job.status not in {"queued", "retrying", "running"}:
            raise SourceEvidenceError("resource_unavailable")
        manifest = MaterializationManifest.model_validate(row.manifest)
        return UploadReservation(
            staging_id=staging_id, object_id=row.object_id,
            fence=version.locator_at_observation["fence"],
            fingerprint=version.provider_revision, state="queued",
            descriptor=_descriptor(manifest.storage.model_dump()), job_id=job.id,
        )

    def load_for_processing(
        self, db: Any, *, staging_id: str, job_id: int, claim: tuple[Any, ...],
    ) -> MaterializedUpload | FinalizedUpload:
        materialization_id = self._materialization_id(staging_id)
        hint = db.get(Materialization, materialization_id)
        if hint is None:
            raise SourceEvidenceError("resource_unavailable")
        upload_scope = UploadScope(owner_id=hint.owner_id, project_id=hint.project_id)
        service, request_scope, row, version = self._bound(db, upload_scope, staging_id)
        job = self._job(db, row, job_id)
        self._require_live_claim(job, claim)
        if row.state == "DERIVED":
            pin = service._pin(request_scope, row)
            descriptor = service.authorize_read(
                db, scope=request_scope, materialization=pin,
                max_bytes=self.max_file_bytes,
            )
            metadata = version.locator_at_observation
            return MaterializedUpload(
                staging_id=staging_id, scope=upload_scope,
                display_name=metadata["display_name"], mime_type=metadata["media_type"],
                checksum=version.integrity[0]["value"], size=metadata["size"],
                descriptor=descriptor, job_id=job_id,
            )
        if row.state == "EXPIRED":
            retired = RetiredMaterializationManifest.model_validate(row.manifest)
            return FinalizedUpload(
                staging_id=staging_id, scope=upload_scope,
                descriptor=_descriptor(retired.storage.model_dump()), job_id=job_id,
                outcome=retired.outcome, result=retired.result,
            )
        if row.state == "PURGED":
            tombstone = PurgeTombstone.model_validate(row.manifest)
            if tombstone.outcome is None:
                raise SourceEvidenceError("resource_unavailable")
            return FinalizedUpload(
                staging_id=staging_id, scope=upload_scope, descriptor=None,
                job_id=job_id, outcome=tombstone.outcome, result=tombstone.result,
            )
        raise SourceEvidenceError("resource_unavailable")

    def finalize(
        self, db: Any, *, scope: UploadScope, staging_id: str, job_id: int,
        claim: tuple[Any, ...], outcome: str, result: Mapping[str, Any] | None = None,
    ) -> CleanupDecision:
        service, request_scope, row, _ = self._bound(db, scope, staging_id)
        self._require_live_claim(self._job(db, row, job_id), claim)
        if outcome == "failed":
            if result is not None or row.state != "DERIVED":
                raise SourceEvidenceError("resource_unavailable")
            return CleanupDecision(False, utc(row.retention_until))
        if outcome not in {"completed", "cancelled"} or row.state != "DERIVED":
            raise SourceEvidenceError("resource_unavailable")
        service.retire(
            db, scope=request_scope, materialization=service._pin(request_scope, row),
            outcome=outcome, result=dict(result) if result is not None else None,
        )
        return CleanupDecision(True)

    def complete_cleanup(
        self, db: Any, *, scope: UploadScope, staging_id: str, job_id: int,
        claim: tuple[Any, ...],
    ) -> None:
        service, request_scope, row, _ = self._bound(db, scope, staging_id)
        self._require_live_claim(self._job(db, row, job_id), claim)
        if row.state == "PURGED":
            return None
        if row.state != "EXPIRED":
            raise SourceEvidenceError("resource_unavailable")
        service.purge(
            db, scope=request_scope, materialization=service._pin(request_scope, row),
        )
        return None

    def _service_audit(
        self, db: Any, row: Materialization, event: str, now: datetime,
        record_version: int,
    ) -> None:
        authority = self.retention_authority
        if authority is None or event not in {
            "MATERIALIZATION_EXPIRED", "MATERIALIZATION_PURGED",
        }:
            raise SourceEvidenceError("resource_unavailable")
        sequence = db.scalar(select(func.coalesce(func.max(AuditExtension.sequence), 0)).where(
            AuditExtension.organization_id == row.organization_id,
            AuditExtension.subject_type == "materialization",
            AuditExtension.subject_id == row.id,
        )) + 1
        ledger = AuditLog(
            action=f"v54.{event}", entity_type="materialization",
            entity_id=None, details=None,
        )
        db.add(ledger)
        db.flush()
        tenant = TaggedId(kind="int", value=str(row.organization_id))
        subject = ObjectRef(
            namespace="pu", type="materialization", tenant_id=tenant,
            id={"kind": "uuid", "value": row.id},
        )
        pin = VersionPin(
            ref=subject, version_kind="record_version", value=record_version,
        )
        db.add(AuditExtension(
            organization_id=row.organization_id, audit_log_id=ledger.id,
            subject_type="materialization", subject_id=row.id,
            sequence=sequence, actor_id=None,
            service_principal=authority.service_principal,
            project_id=row.project_id,
            correlation_id=_stable_uuid(
                "retention", f"{row.organization_id}:{row.project_id}:{row.id}",
            ),
            action_pin=None, subject_pin=pin.model_dump(mode="json"),
            approval_id=None, receipt_id=None, job_id=None, relation_refs=[],
        ))
        db.flush()

    def _retention_binding(
        self, db: Any, materialization_id: str,
    ) -> tuple[Materialization, BackgroundJob, str]:
        authority = self.retention_authority
        if authority is None:
            raise SourceEvidenceError("resource_unavailable")
        row = db.scalar(select(Materialization).where(
            Materialization.id == materialization_id,
            Materialization.state.in_(("DERIVED", "EXPIRED")),
        ).with_for_update())
        if row is None:
            raise SourceEvidenceError("resource_unavailable")
        authority.require(db, row)
        source = db.scalar(select(SourceReference).where(
            SourceReference.id == row.source_id,
            SourceReference.organization_id == row.organization_id,
            SourceReference.origin_project_id == row.project_id,
            SourceReference.namespace == "local-upload",
        ))
        if source is None:
            raise SourceEvidenceError("resource_unavailable")
        job = self._job(db, row)
        if (
            job is None or job.status not in {"failed", "dead_letter"}
            or job.payload != {"staging_id": self._staging_id(row.id)}
        ):
            raise SourceEvidenceError("resource_unavailable")
        outcome = job.status
        if row.state == "EXPIRED":
            retired = RetiredMaterializationManifest.model_validate(row.manifest)
            if retired.outcome != outcome:
                raise SourceEvidenceError("resource_unavailable")
        return row, job, outcome

    def recover_retention(self, session_factory: Callable[[], Any], *, limit: int) -> int:
        """Expire, delete, then tombstone failed local uploads without user authority."""
        if (
            self.retention_authority is None or not callable(session_factory)
            or type(limit) is not int or not 1 <= limit <= 500
        ):
            raise SourceEvidenceError("resource_unavailable")
        now = utc(self.clock())
        with session_factory() as db:
            candidates = list(db.scalars(select(Materialization.id).join(
                SourceReference,
                (SourceReference.id == Materialization.source_id)
                & (SourceReference.organization_id == Materialization.organization_id),
            ).where(
                SourceReference.namespace == "local-upload",
                Materialization.state.in_(("DERIVED", "EXPIRED")),
                Materialization.retention_until <= now,
            ).order_by(Materialization.retention_until, Materialization.id).limit(limit * 4)))

        purged = 0
        for materialization_id in candidates:
            if purged >= limit:
                break
            try:
                with session_factory() as db:
                    row, _, outcome = self._retention_binding(db, materialization_id)
                    if utc(row.retention_until) > now:
                        raise SourceEvidenceError("resource_unavailable")
                    if row.state == "DERIVED":
                        storage = MaterializationManifest.model_validate(row.manifest).storage
                        next_version = row.record_version + 1
                        cas(db, Materialization, [
                            Materialization.id == row.id,
                            Materialization.organization_id == row.organization_id,
                            Materialization.project_id == row.project_id,
                            Materialization.owner_id == row.owner_id,
                            Materialization.state == "DERIVED",
                        ], row.record_version, state="EXPIRED", expired_at=now,
                            active_fence=None, manifest=RetiredMaterializationManifest(
                                schema_version="v54.materialization.retired.1",
                                storage=storage, outcome=outcome,
                            ).model_dump(mode="json"))
                        row = db.get(Materialization, materialization_id)
                        self._service_audit(
                            db, row, "MATERIALIZATION_EXPIRED", now, next_version,
                        )
                        db.commit()
                    descriptor = _descriptor(
                        RetiredMaterializationManifest.model_validate(row.manifest)
                        .storage.model_dump()
                    )

                self.storage.delete(descriptor.object_id)

                with session_factory() as db:
                    row, _, outcome = self._retention_binding(db, materialization_id)
                    if row.state != "EXPIRED":
                        raise SourceEvidenceError("resource_unavailable")
                    next_version = row.record_version + 1
                    cas(db, Materialization, [
                        Materialization.id == row.id,
                        Materialization.organization_id == row.organization_id,
                        Materialization.project_id == row.project_id,
                        Materialization.owner_id == row.owner_id,
                        Materialization.state == "EXPIRED",
                    ], row.record_version, state="PURGED",
                        manifest=PurgeTombstone(
                            schema_version="v54.materialization.tombstone.1",
                            outcome=outcome,
                        ).model_dump(mode="json", exclude_none=True),
                        wrapped_dek=None, format_version=None, chunk_size=None,
                        active_fence=None, purged_at=now)
                    row = db.get(Materialization, materialization_id)
                    self._service_audit(
                        db, row, "MATERIALIZATION_PURGED", now, next_version,
                    )
                    db.commit()
                purged += 1
            except Exception:
                # Per-object fail closed. No locator, exception text, or content is logged.
                continue
        return purged
