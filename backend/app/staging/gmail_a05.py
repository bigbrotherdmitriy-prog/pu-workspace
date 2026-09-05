"""Concrete Gmail attachment port over the authoritative a05 lifecycle.

The adapter deliberately has no provider client and no queue implementation.
Its policy factory is server composition: HTTP input cannot choose residency,
retention, keys, derive classes, or the materialization authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager, FrozenSet
from uuid import UUID, uuid5

from sqlalchemy import select

from app.core.integration_types import StorageObject
from app.core.v54_dto import canonical_hash
from app.core.v54_interfaces import RequestScope
from app.core.v54_permissions import SourceEvidenceError, object_ref, utc
from app.core.v54_refs import VersionPin
from app.jobs.queue import utcnow as queue_now
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.job import BackgroundJob
from app.models.materialization import Materialization
from app.models.v54_pilot import Evidence, SourceCurrent, SourceReference, SourceVersion
from app.staging.contracts import KekRef, StagingError, StagingIntegrityError, StagingStorage
from app.staging.filesystem import new_fence
from app.staging.gmail import (
    GMAIL_ATTACHMENT_JOB_KIND,
    GmailAttachmentBinding,
    GmailAttachmentDenied,
    GmailAttachmentIntegrityError,
    GmailAttachmentJobClaim,
    GmailAttachmentProcessResult,
    GmailAttachmentProvider,
    GmailAttachmentStageResult,
    validate_gmail_attachment_binding,
)
from app.staging.lifecycle import LifecycleAuthority, MaterializationLifecycle, PurgeTombstone


_NAMESPACE = UUID("9faea2dd-815d-4a56-afeb-428a774abf92")
_SCHEMA = "v54.gmail-attachment-materialization.1"
_DERIVE_CLASSES = frozenset({"document", "task", "draft", "risk", "decision"})
DEFAULT_NATIVE_MIME_TYPES = frozenset({
    "application/json", "text/csv", "text/markdown", "text/plain",
})


@dataclass(frozen=True, slots=True)
class GmailAttachmentPolicyDecision:
    """Server-owned, exact admission decision; absence always denies."""

    authority: LifecycleAuthority
    residency: str
    retention: timedelta
    failed_retention: timedelta
    kek: KekRef
    allowed_mime_types: FrozenSet[str]
    derive_classes: FrozenSet[str]
    copy_allowed: bool
    backup_allowed: bool = False

    def __post_init__(self) -> None:
        maximum = self.authority.max_retention if isinstance(self.authority, LifecycleAuthority) else None
        if (
            not isinstance(self.authority, LifecycleAuthority)
            or type(self.residency) is not str
            or self.residency not in self.authority.allowed_residencies
            or not isinstance(self.retention, timedelta)
            or not isinstance(self.failed_retention, timedelta)
            or self.retention <= timedelta(0)
            or self.failed_retention <= timedelta(0)
            or self.failed_retention > self.retention
            or maximum is None
            or self.retention > maximum
            or self.kek not in self.authority.allowed_keks
            or type(self.copy_allowed) is not bool
            or self.copy_allowed is not True
            or self.authority.copy_allowed is not True
            or self.authority.derive_allowed is not True
            or self.authority.retention_owner is not True
            or type(self.backup_allowed) is not bool
            or self.backup_allowed is not False
            or not isinstance(self.allowed_mime_types, frozenset)
            or not self.allowed_mime_types
            or not self.allowed_mime_types <= DEFAULT_NATIVE_MIME_TYPES
            or not isinstance(self.derive_classes, frozenset)
            or "document" not in self.derive_classes
            or not self.derive_classes <= _DERIVE_CLASSES
            or (("risk" in self.derive_classes) != ("decision" in self.derive_classes))
        ):
            raise GmailAttachmentDenied("attachment_policy_denied")


def _stable_uuid(label: str, value: str) -> str:
    return str(uuid5(_NAMESPACE, f"{label}\x00{value}"))


def _staging_id(materialization_id: str) -> str:
    try:
        return UUID(materialization_id).hex
    except (ValueError, AttributeError):
        raise GmailAttachmentDenied("staging_unavailable") from None


def _materialization_id(staging_id: str) -> str:
    try:
        if type(staging_id) is not str or len(staging_id) != 32:
            raise ValueError
        return str(UUID(hex=staging_id))
    except (ValueError, AttributeError):
        raise GmailAttachmentDenied("staging_unavailable") from None


def _pin(scope: RequestScope, kind: str, identity: str, value: int = 1) -> VersionPin:
    return VersionPin(
        ref=object_ref(scope, kind, identity),
        version_kind="record_version" if kind == "source" else "revision",
        value=value,
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class _GuardedSession:
    """Keep legacy processors in one T2 transaction and guard every commit."""

    def __init__(self, db: Any, guard: Callable[[], None]):
        self._db, self._guard = db, guard

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def commit(self) -> None:
        self._guard()
        self._db.flush()


class A05GmailAttachmentLifecycle:
    """Durable implementation of :class:`GmailAttachmentLifecyclePort`."""

    def __init__(
        self, *, storage: StagingStorage,
        policy_factory: Callable[[Any, GmailAttachmentBinding], GmailAttachmentPolicyDecision],
        clock: Callable[[], datetime], max_bytes: int,
    ) -> None:
        if (
            not isinstance(storage, StagingStorage)
            or not callable(policy_factory)
            or not callable(clock)
            or type(max_bytes) is not int
            or max_bytes <= 0
        ):
            raise GmailAttachmentDenied("attachment_policy_denied")
        self.storage = storage
        self.policy_factory = policy_factory
        self.clock = clock
        self.max_bytes = max_bytes

    def _authorize_binding(self, binding: GmailAttachmentBinding) -> None:
        """Use a fresh transaction at commit boundaries, just like provider open."""
        from app.database import SessionLocal
        with SessionLocal() as auth_db:
            validate_gmail_attachment_binding(auth_db, binding, max_bytes=self.max_bytes)

    def _scope(self, binding: GmailAttachmentBinding) -> RequestScope:
        tenant = {"kind": "int", "value": str(binding.organization_id)}
        return RequestScope(
            actor={"namespace": "pu", "type": "user", "tenant_id": tenant,
                   "id": {"kind": "int", "value": str(binding.owner_user_id)}},
            tenant=tenant,
            project={"namespace": "pu", "type": "project", "tenant_id": tenant,
                     "id": {"kind": "int", "value": str(binding.project_id)}},
            correlation_id=_stable_uuid(
                "correlation",
                f"{binding.organization_id}:{binding.owner_user_id}:{binding.project_id}:gmail",
            ),
        )

    def _decision(
        self, db: Any, binding: GmailAttachmentBinding,
    ) -> tuple[GmailAttachmentPolicyDecision, MaterializationLifecycle, RequestScope]:
        try:
            decision = self.policy_factory(db, binding)
        except Exception:
            raise GmailAttachmentDenied("attachment_policy_denied") from None
        if not isinstance(decision, GmailAttachmentPolicyDecision):
            raise GmailAttachmentDenied("attachment_policy_denied")
        policy = decision.authority.policy
        if (
            policy.tenant_id != binding.organization_id
            or policy.project_id != binding.project_id
            or binding.declared_mime_type not in decision.allowed_mime_types
            or binding.declared_size > self.max_bytes
            or binding.mode != "CONFIRM"
        ):
            raise GmailAttachmentDenied("attachment_policy_denied")
        scope = self._scope(binding)
        service = MaterializationLifecycle(decision.authority, self.storage, self.clock)
        now = service._now()
        for operation in ("write", "observe", "audit", "fragment"):
            policy.require(db, scope, operation, now, lock=True)
        return decision, service, scope

    @staticmethod
    def _policy_metadata(
        decision: GmailAttachmentPolicyDecision, retention_until: datetime,
    ) -> dict[str, Any]:
        policy = decision.authority.policy
        return {
            "policy_pins": policy.policy_pins(),
            "residency": decision.residency,
            "retention_until": retention_until.isoformat(),
            "failed_retention_seconds": int(decision.failed_retention.total_seconds()),
            "kek_reference": decision.kek.reference,
            "kek_version": decision.kek.version,
            "copy_allowed": decision.copy_allowed,
            "backup_allowed": decision.backup_allowed,
            "derive_classes": sorted(decision.derive_classes),
            "allowed_mime_types": sorted(decision.allowed_mime_types),
        }

    def _binding_metadata(
        self, binding: GmailAttachmentBinding, decision: GmailAttachmentPolicyDecision,
        retention_until: datetime,
    ) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA,
            "binding": asdict(binding),
            "policy": self._policy_metadata(decision, retention_until),
        }

    @staticmethod
    def _decode_binding(version: SourceVersion) -> GmailAttachmentBinding:
        metadata = version.locator_at_observation
        if type(metadata) is not dict or set(metadata) != {"schema_version", "binding", "policy"}:
            raise GmailAttachmentDenied("staging_unavailable")
        if metadata.get("schema_version") != _SCHEMA or type(metadata.get("policy")) is not dict:
            raise GmailAttachmentDenied("staging_unavailable")
        try:
            return GmailAttachmentBinding(**metadata["binding"])
        except (TypeError, ValueError):
            raise GmailAttachmentDenied("staging_unavailable") from None

    def _load(self, db: Any, staging_id: str, *, lock: bool = True):
        query = select(Materialization).where(
            Materialization.id == _materialization_id(staging_id),
        ).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        row = db.scalar(query)
        version = db.get(SourceVersion, row.source_version_id) if row else None
        source = db.get(SourceReference, row.source_id) if row else None
        current = db.get(SourceCurrent, row.source_id) if row else None
        evidence = db.get(Evidence, row.evidence_id) if row else None
        if not row or not version or not source or not current or not evidence:
            raise GmailAttachmentDenied("staging_unavailable")
        binding = self._decode_binding(version)
        if (
            row.organization_id != binding.organization_id
            or row.project_id != binding.project_id
            or row.owner_id != binding.owner_user_id
            or source.organization_id != binding.organization_id
            or source.origin_project_id != binding.project_id
            or source.identity_id != binding.identity_id
            or source.namespace != "gmail"
            or source.parent_source_id != binding.source_reference_id
            or source.object_kind != "attachment"
            or source.external_id != canonical_hash(asdict(binding))
            or version.organization_id != row.organization_id
            or version.source_id != source.id
            or current.organization_id != row.organization_id
            or current.version_id != version.id
            or evidence.organization_id != row.organization_id
            or evidence.source_id != source.id
            or evidence.source_version_id != version.id
        ):
            raise GmailAttachmentDenied("staging_unavailable")
        return row, source, version, evidence, binding

    @staticmethod
    def _same_policy(
        version: SourceVersion, decision: GmailAttachmentPolicyDecision, row: Materialization,
    ) -> bool:
        expected = A05GmailAttachmentLifecycle._policy_metadata(
            decision, _aware(row.retention_until),
        )
        return version.locator_at_observation.get("policy") == expected

    def _existing_for_binding(self, db: Any, binding: GmailAttachmentBinding):
        digest = canonical_hash(asdict(binding))
        source_id = _stable_uuid("source", digest)
        source = db.get(SourceReference, source_id)
        if source is None:
            return None
        row = db.scalar(select(Materialization).where(
            Materialization.source_id == source.id,
            Materialization.organization_id == binding.organization_id,
            Materialization.project_id == binding.project_id,
            Materialization.owner_id == binding.owner_user_id,
        ).with_for_update())
        if row is None:
            raise GmailAttachmentDenied("staging_unavailable")
        loaded = self._load(db, _staging_id(row.id))
        if loaded[-1] != binding:
            raise GmailAttachmentDenied("staging_unavailable")
        return loaded

    def _create_admission(
        self, db: Any, binding: GmailAttachmentBinding,
        decision: GmailAttachmentPolicyDecision, service: MaterializationLifecycle,
        scope: RequestScope,
    ):
        now = service._now()
        retention_until = now + decision.retention
        digest = canonical_hash(asdict(binding))
        source_id = _stable_uuid("source", digest)
        version_id = _stable_uuid("version", digest)
        evidence_id = _stable_uuid("evidence", digest)
        source = SourceReference(
            id=source_id, organization_id=binding.organization_id,
            origin_project_id=binding.project_id, identity_id=binding.identity_id,
            parent_source_id=binding.source_reference_id, namespace="gmail",
            external_id=digest, external_id_kind="binding_sha256",
            incarnation=1, object_kind="attachment",
            canonical_locator={"kind": "opaque_binding", "normalization_version": "1"},
            record_version=1,
            freshness="fresh", sync_state="observed", availability="available",
            last_seen_at=now, last_checked_at=now, next_check_at=retention_until,
            policy_pins=decision.authority.policy.policy_pins(),
            residency={"location": decision.residency, "backup_allowed": False},
        )
        db.add(source)
        db.flush()
        version = SourceVersion(
            id=version_id, organization_id=binding.organization_id, source_id=source.id,
            revision=1, observation_key=digest, provider_revision=None,
            consistency="metadata_only",
            locator_at_observation=self._binding_metadata(binding, decision, retention_until),
            integrity=[{"algorithm": "aead-aes256-gcm", "format": "v54.encrypted-staging.2"}],
            observed_at=now,
        )
        db.add(version)
        db.flush()
        db.add(SourceCurrent(
            source_id=source.id, organization_id=binding.organization_id,
            version_id=version.id,
        ))
        evidence = Evidence(
            id=evidence_id, organization_id=binding.organization_id,
            source_id=source.id, source_version_id=version.id, revision=1,
            locator={"kind": "whole_attachment", "attachment_index": binding.attachment_index},
            extractor={"name": "gmail_attachment", "version": "1", "ocr": False},
            confidence=1.0, confidence_kind="provider", extracted_at=now,
            policy_pins=decision.authority.policy.policy_pins(),
        )
        db.add(evidence)
        db.flush()
        admitted = service.admit(
            db, scope=scope, evidence=_pin(scope, "evidence", evidence.id),
            source_version=_pin(scope, "source_version", version.id),
            residency=decision.residency, retention_until=retention_until,
            kek=decision.kek, allow_copy=False, allow_derive=True,
        )
        writing = service.begin_write(
            db, scope=scope, materialization=admitted, fence=new_fence(),
        )
        row = db.get(Materialization, writing.ref.id.value, populate_existing=True)
        staging_id = _staging_id(row.id)
        db.commit()
        return self._load(db, staging_id), writing

    def admit_and_stage(
        self, db: Any, binding: GmailAttachmentBinding, provider: GmailAttachmentProvider,
    ) -> GmailAttachmentStageResult:
        decision, service, scope = self._decision(db, binding)
        existing = self._existing_for_binding(db, binding)
        duplicate = existing is not None
        if existing is None:
            try:
                existing, writing = self._create_admission(
                    db, binding, decision, service, scope,
                )
            except Exception as error:
                db.rollback()
                # A concurrent identical ingress may have won the deterministic
                # SourceReference insert. Reload it without exposing SQL values.
                existing = self._existing_for_binding(db, binding)
                if existing is None:
                    if isinstance(error, GmailAttachmentDenied):
                        raise
                    raise GmailAttachmentDenied("staging_unavailable") from None
                duplicate = True
                row = existing[0]
                writing = service._pin(scope, row)
        else:
            row = existing[0]
            writing = service._pin(scope, row)
        row, _source, version, _evidence, _ = existing
        if not self._same_policy(version, decision, row):
            raise GmailAttachmentDenied("attachment_policy_changed")
        staging_id = _staging_id(row.id)
        if row.state in {"DERIVED", "EXPIRED", "PURGED"}:
            return GmailAttachmentStageResult(staging_id, duplicate=True)
        if row.state == "SEALED":
            self._authorize_binding(binding)
            service.derive(db, scope=scope, materialization=writing)
            self._authorize_binding(binding)
            db.commit()
            return GmailAttachmentStageResult(staging_id, duplicate=True)
        if row.state != "WRITING" or not row.active_fence:
            raise GmailAttachmentDenied("staging_unavailable")
        fence = row.active_fence
        try:
            opened = provider.open()
            if opened.observed_size != binding.declared_size:
                raise GmailAttachmentIntegrityError("provider_attachment_size_mismatch")
            sealed = service.seal(
                db, scope=scope, materialization=writing, fence=fence,
                source=opened.stream, max_bytes=self.max_bytes,
                kind="extracted_text", media_type=binding.declared_mime_type,
            )
            self._authorize_binding(binding)
            service.derive(db, scope=scope, materialization=sealed)
            self._authorize_binding(binding)
            db.commit()
        except GmailAttachmentIntegrityError:
            db.rollback()
            self._purge(db, staging_id)
            raise
        except GmailAttachmentDenied:
            db.rollback()
            self._purge(db, staging_id)
            raise
        except StagingIntegrityError:
            db.rollback()
            self._purge(db, staging_id)
            raise GmailAttachmentIntegrityError("ciphertext_integrity_failed") from None
        except (StagingError, SourceEvidenceError):
            db.rollback()
            self._purge(db, staging_id)
            raise GmailAttachmentDenied("attachment_materialization_denied") from None
        except Exception:
            db.rollback()
            self._purge(db, staging_id)
            raise GmailAttachmentIntegrityError("provider_attachment_invalid") from None
        return GmailAttachmentStageResult(staging_id, duplicate=duplicate)

    def describe(self, db: Any, staging_id: str) -> GmailAttachmentBinding:
        return self._load(db, staging_id, lock=False)[-1]

    @staticmethod
    def _require_claim(db: Any, claim: GmailAttachmentJobClaim, staging_id: str) -> BackgroundJob:
        job = db.scalar(select(BackgroundJob).where(
            BackgroundJob.id == claim.job_id,
        ).execution_options(populate_existing=True).with_for_update())
        if (
            not job
            or job.kind != GMAIL_ATTACHMENT_JOB_KIND
            or job.payload != {"staging_id": staging_id}
            or job.status != "running"
            or job.worker_id != claim.worker_id
            or job.attempts != claim.attempt
            or _aware(job.locked_at) != _aware(claim.locked_at)
            or _aware(job.lease_expires_at) is None
            or _aware(job.lease_expires_at) <= queue_now()
        ):
            raise GmailAttachmentDenied("stale_job_claim")
        return job

    def _live(
        self, db: Any, staging_id: str, binding: GmailAttachmentBinding,
        claim: GmailAttachmentJobClaim,
    ):
        self._require_claim(db, claim, staging_id)
        decision, service, scope = self._decision(db, binding)
        row, _source, version, _evidence, current = self._load(db, staging_id)
        if current != binding or row.state != "DERIVED" or not self._same_policy(version, decision, row):
            raise GmailAttachmentDenied("staging_unavailable")
        service._require(db, scope, "fragment")
        return decision, service, scope, row

    def _read(
        self, db: Any, staging_id: str, binding: GmailAttachmentBinding,
        claim: GmailAttachmentJobClaim,
        authorize_read: Callable[[], ContextManager[None]],
    ) -> bytes:
        decision, service, scope, row = self._live(db, staging_id, binding, claim)
        descriptor = service._storage_descriptor(row)
        iterator = self.storage.read_chunks(descriptor, max_bytes=self.max_bytes)
        result = bytearray()
        try:
            while True:
                try:
                    with authorize_read():
                        self._live(db, staging_id, binding, claim)
                        chunk = next(iterator)
                except StopIteration:
                    break
                if not isinstance(chunk, bytes) or len(result) + len(chunk) > self.max_bytes:
                    raise GmailAttachmentIntegrityError("ciphertext_integrity_failed")
                result.extend(chunk)
        except GmailAttachmentIntegrityError:
            raise
        except StagingIntegrityError:
            raise GmailAttachmentIntegrityError("ciphertext_integrity_failed") from None
        except StagingError:
            raise GmailAttachmentDenied("staging_unavailable") from None
        if len(result) != binding.declared_size:
            raise GmailAttachmentIntegrityError("ciphertext_integrity_failed")
        return bytes(result)

    @staticmethod
    def _native_text(content: bytes, mime_type: str) -> str:
        if mime_type not in DEFAULT_NATIVE_MIME_TYPES:
            raise GmailAttachmentDenied("attachment_derive_denied")
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise GmailAttachmentIntegrityError("attachment_text_invalid") from None
        return " ".join(text.split())[:250_000]

    def process(
        self, db: Any, staging_id: str, claim: GmailAttachmentJobClaim,
        authorize_read: Callable[[], ContextManager[None]],
    ) -> GmailAttachmentProcessResult:
        row, _source, version, _evidence, binding = self._load(db, staging_id)

        def guard() -> None:
            with authorize_read():
                self._live(db, staging_id, binding, claim)

        guard()
        external_id = f"gmail-staging:{staging_id}"
        existing = db.scalar(select(Document).where(
            Document.project_id == binding.project_id,
            Document.external_id == external_id,
        ))
        if existing is not None:
            return GmailAttachmentProcessResult("completed", document_id=existing.id)
        content = self._read(db, staging_id, binding, claim, authorize_read)
        with authorize_read():
            self._live(db, staging_id, binding, claim)
            text = self._native_text(content, binding.declared_mime_type)
        if not text:
            raise GmailAttachmentDenied("attachment_text_unavailable")
        decision, _service, _scope, _row = self._live(db, staging_id, binding, claim)
        extension = {"text/plain": "txt", "text/markdown": "md", "text/csv": "csv",
                     "application/json": "json"}[binding.declared_mime_type]
        item = StorageObject(
            id=external_id, name=f"Gmail attachment {binding.attachment_index + 1}.{extension}",
            mime_type=binding.declared_mime_type, parent_id=f"message:{binding.message_id}",
            size=binding.declared_size, content_text=text,
        )
        guarded_db = _GuardedSession(db, guard)
        from app.document_engine import index_documents
        from app.governance_engine import create_governance_items
        from app.response_engine import create_response_drafts
        from app.task_engine import create_tasks_from_files

        documents = index_documents(guarded_db, binding.project_id, [item], "gmail")
        tasks = (create_tasks_from_files(
            guarded_db, binding.project_id, None, [item], source_type="email_attachment",
        ) if "task" in decision.derive_classes else [])
        drafts = (create_response_drafts(
            guarded_db, binding.project_id, None, [item],
        ) if "draft" in decision.derive_classes else [])
        risks, decisions = (create_governance_items(
            guarded_db, binding.project_id, [item], source_type="email_attachment",
        ) if decision.derive_classes & {"risk", "decision"} else ([], []))
        guard()
        db.add(AuditLog(
            action="gmail_attachment_materialized", entity_type="materialization",
            entity_id=None, details=None,
        ))
        db.commit()
        return GmailAttachmentProcessResult(
            "completed", document_id=documents[0].id,
            tasks=len(tasks), drafts=len(drafts), risks=len(risks), decisions=len(decisions),
        )

    def _job(self, db: Any, staging_id: str) -> BackgroundJob | None:
        job = db.scalar(select(BackgroundJob).where(
            BackgroundJob.idempotency_key == f"{GMAIL_ATTACHMENT_JOB_KIND}:{staging_id}",
        ).with_for_update())
        if job is not None and (
            job.kind != GMAIL_ATTACHMENT_JOB_KIND or job.payload != {"staging_id": staging_id}
        ):
            raise GmailAttachmentDenied("staging_unavailable")
        return job

    def _purge(self, db: Any, staging_id: str) -> None:
        row, _source, _version, _evidence, _binding = self._load(db, staging_id)
        if row.state == "PURGED":
            return
        if row.active_fence:
            self.storage.cleanup_partials(
                row.object_id, eligible_fences={row.active_fence}, active_fences=set(),
            )
        self.storage.delete(row.object_id)
        now = _aware(self.clock())
        row.state = "PURGED"
        row.record_version += 1
        row.active_fence = None
        row.format_version = None
        row.chunk_size = None
        row.wrapped_dek = None
        row.manifest = PurgeTombstone(
            schema_version="v54.materialization.tombstone.1",
        ).model_dump(mode="json")
        row.expired_at = row.expired_at or now
        row.purged_at = now
        db.add(AuditLog(
            action="gmail_attachment_purged", entity_type="materialization",
            entity_id=None, details=None,
        ))
        db.commit()

    def on_job_outcome(self, db: Any, staging_id: str, status: str) -> None:
        row, _source, version, _evidence, _binding = self._load(db, staging_id)
        job = self._job(db, staging_id)
        if job is None:
            raise GmailAttachmentDenied("staging_unavailable")
        if job.status != status:
            # Hooks are notifications, never authority. A delayed duplicate
            # cannot reverse or extend the durable queue/materialization state.
            return
        integrity_failure = job.last_error in {
            "GmailAttachmentIntegrityError", "StagingIntegrityError",
        }
        if status in {"completed", "cancelled"} or integrity_failure:
            self._purge(db, staging_id)
            return
        if status in {"failed", "dead_letter", "retrying"}:
            policy = version.locator_at_observation.get("policy", {})
            failed_seconds = policy.get("failed_retention_seconds")
            failure_at = _aware(job.completed_at or job.updated_at)
            failed_until = (
                failure_at + timedelta(seconds=failed_seconds)
                if failure_at is not None and type(failed_seconds) is int and failed_seconds > 0
                else _aware(row.retention_until)
            )
            deadline = min(_aware(row.retention_until), failed_until)
            if status != "retrying" and deadline <= _aware(self.clock()):
                self._purge(db, staging_id)
            return
        raise GmailAttachmentDenied("staging_unavailable")

    def recover_pending(self, db: Any, limit: int):
        now = _aware(self.clock())
        rows = list(db.scalars(select(Materialization).where(
            Materialization.state.in_(("WRITING", "SEALED", "DERIVED", "EXPIRED")),
        ).order_by(Materialization.admitted_at, Materialization.id).limit(limit * 4)))
        dispatch: list[str] = []
        for row in rows:
            source = db.get(SourceReference, row.source_id)
            if not source or source.namespace != "gmail" or source.object_kind != "attachment":
                continue
            staging_id = _staging_id(row.id)
            try:
                self._load(db, staging_id)
            except GmailAttachmentDenied:
                continue
            job = self._job(db, staging_id)
            if job is not None and job.status in {"completed", "cancelled"}:
                self._purge(db, staging_id)
                continue
            if job is not None and job.last_error in {
                "GmailAttachmentIntegrityError", "StagingIntegrityError",
            }:
                self._purge(db, staging_id)
                continue
            failed_deadline = None
            if job is not None and job.status in {"failed", "dead_letter"}:
                policy = row and db.get(SourceVersion, row.source_version_id).locator_at_observation.get(
                    "policy", {},
                )
                seconds = policy.get("failed_retention_seconds") if isinstance(policy, dict) else None
                failure_at = _aware(job.completed_at or job.updated_at)
                if failure_at is not None and type(seconds) is int and seconds > 0:
                    failed_deadline = failure_at + timedelta(seconds=seconds)
            deadline = min(
                _aware(row.retention_until),
                failed_deadline or _aware(row.retention_until),
            )
            if deadline <= now or row.state == "EXPIRED":
                self._purge(db, staging_id)
                continue
            if row.state == "SEALED" and job is None:
                _row, _source, version, _evidence, binding = self._load(db, staging_id)
                try:
                    decision, service, scope = self._decision(db, binding)
                    if not self._same_policy(version, decision, row):
                        raise GmailAttachmentDenied("attachment_policy_changed")
                    self._authorize_binding(binding)
                    service.derive(db, scope=scope, materialization=service._pin(scope, row))
                    self._authorize_binding(binding)
                    db.commit()
                    row = db.get(Materialization, row.id, populate_existing=True)
                except Exception:
                    db.rollback()
                    continue
            if row.state == "DERIVED" and job is None:
                dispatch.append(staging_id)
                if len(dispatch) == limit:
                    break
        return tuple(dispatch)
