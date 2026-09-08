"""Bounded XLSX children in the existing encrypted materialization registry.

Admission/fences commit before I/O. All fragments publish together; the existing
local-upload job owns replay, and its terminal state gates retention cleanup.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from io import BytesIO
from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field, StrictBool, StrictStr
from sqlalchemy import inspect, select

from app.core.v54_permissions import SourceEvidenceError, deny, object_ref, utc
from app.core.v54_authority import AuthorityResolver
from app.core.v54_refs import StrictDTO, VersionPin
from app.jobs.queue import current_execution_claim
from app.models.materialization import Materialization
from app.models.job import BackgroundJob
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import ConnectionIdentity, Evidence, EvidenceAssessment, SourceReference, SourceVersion
from app.source_evidence.common import audit, cas
from app.source_evidence.fragment_reader import SheetCellLocator, ExtractorMetadata, RepresentationDescriptor
from app.source_evidence.local_source import LocalUploadObservation, require_local_upload_source
from app.staging.contracts import KekRef
from app.staging.filesystem import new_fence
from app.staging.lifecycle import MaterializationManifest, PurgeTombstone
from app.staging.local_upload import A05LocalUploadLifecycle, LocalUploadRetentionAuthority, _stable_uuid

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_FRAGMENTS = 512
MAX_FRAGMENT_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
_NAMESPACE = UUID("3bce7d77-bbb4-4c66-b2fb-532ca4d0716c")
_EXTRACTOR = {"name": "xlsx_cells", "version": "1", "method": "native_no_recalculation"}
_CONFIG_DIGEST = hashlib.sha256(b"v54.xlsx_cell.1;exact-formula-cache;no-recalculation;no-external-resolution;limits=512,262144,4194304").hexdigest()


class XlsxCellSnapshot(StrictDTO):
    schema_version: Literal["v54.xlsx_cell.1"]
    locator: SheetCellLocator
    original_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    value: StrictStr = Field(max_length=32767)
    cache_state: Literal["present", "empty", "missing"]
    cache_freshness: Literal["not_verified"]
    formula_recalculated: Literal[False]
    manual_review_required: StrictBool
    formula_type: StrictStr | None


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _ids(version_id, locator):
    key = version_id + "\x00" + _json(locator)
    evidence = str(uuid5(_NAMESPACE, "evidence:1:" + key))
    materialization = str(uuid5(_NAMESPACE, "materialization:1:" + key))
    return evidence, materialization, uuid5(_NAMESPACE, "object:1:" + key).hex


def _pin(scope, kind, identity, value=1):
    return VersionPin(ref=object_ref(scope, kind, identity),
                      version_kind="record_version" if kind == "materialization" else "revision", value=value)


def _plans(extraction, checksum, version_id):
    plans, total, seen = [], 0, set()
    for cell in extraction.spreadsheet_cells:
        if cell.get("identity_verified") is not True:
            deny()
        for raw in cell["locators"]:
            locator = SheetCellLocator.model_validate(raw)
            if locator.value_kind not in {"formula", "cached_value"}:
                deny()
            value = cell["formula"] if locator.value_kind == "formula" else cell["cached_value"]
            payload = XlsxCellSnapshot(schema_version="v54.xlsx_cell.1", locator=locator,
                original_sha256=checksum, value=value, cache_state=cell["cache_state"],
                cache_freshness="not_verified", formula_recalculated=False,
                manual_review_required=cell["needs_review"], formula_type=cell["formula_type"])
            data = _json(payload.model_dump(mode="json")).encode("utf-8")
            raw_locator = locator.model_dump(mode="json")
            identity = _json(raw_locator)
            if identity in seen:
                deny()
            seen.add(identity)
            total += len(data)
            if len(data) > MAX_FRAGMENT_BYTES or total > MAX_TOTAL_BYTES or len(plans) >= MAX_FRAGMENTS:
                deny()
            plans.append((*_ids(version_id, raw_locator), raw_locator, data,
                          {**_EXTRACTOR, "configuration_digest": _CONFIG_DIGEST}))
    if not plans:
        deny()
    return plans


class XlsxEvidenceIngestion:
    """Explicit server composition; it never creates permissions or a queue."""

    def __init__(self, lifecycle: A05LocalUploadLifecycle):
        if not isinstance(lifecycle, A05LocalUploadLifecycle):
            deny()
        self.lifecycle = lifecycle

    def _live(self, db, record):
        backend = self.lifecycle
        if not db.in_transaction():
            db.begin()
        if any(isinstance(row, (BackgroundJob, Project, ProjectMember, User, AuthorityState))
               for row in set(db.new) | set(db.dirty) | set(db.deleted)):
            deny()
        # Match the reader's project/authority -> materialization lock order.
        _, request_scope = backend._service(db, record.scope)
        AuthorityResolver(clock=backend.clock).require(db, request_scope, "write", utc(backend.clock()), lock=True)
        service, scope, original, version = backend._bound(db, record.scope, record.staging_id)
        claim = current_execution_claim()
        job = backend._job(db, original, record.job_id)
        if job is not None:
            job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job.id)
                            .with_for_update().execution_options(populate_existing=True))
        backend._require_live_claim(job, claim)
        if (job.cancelled_at is not None or job.payload != {"staging_id": record.staging_id}
                or original.state != "DERIVED"):
            deny()
        binding = require_local_upload_source(db, scope=scope,
            source_ref=object_ref(scope, "source", original.source_id),
            source_version_pin=_pin(scope, "source_version", original.source_version_id),
            operation="write", lock=True, clock=backend.clock)
        metadata = version.locator_at_observation
        if (record.source_version_id != version.id or record.checksum != binding.checksum_sha256
                or record.size != binding.size or record.mime_type != binding.media_type or record.mime_type != MIME
                or record.display_name != metadata["display_name"]
                or record.descriptor != service._storage_descriptor(original)
                or not service.authority.derive_allowed
                or not isinstance(backend.retention_authority, LocalUploadRetentionAuthority)):
            deny()
        backend.retention_authority.require(db, original)
        return service, scope, original, binding

    def publish(self, db, *, record, content, extraction):
        """Commit durable admission, then atomically publish all bounded cells."""
        try:
            if (not isinstance(content, bytes) or len(content) != record.size
                    or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), record.checksum)):
                deny()
            service, scope, original, binding = self._live(db, record)
            plans = _plans(extraction, record.checksum, binding.source_version_pin.ref.id.value)
            original_id, version_id = original.id, original.source_version_id
            existing = list(db.scalars(select(Materialization).where(Materialization.parent_id == original_id)))
            if existing and {row.id for row in existing} != {plan[1] for plan in plans}:
                deny()
            states = []
            for evidence_id, materialization_id, object_id, locator, _, extractor in plans:
                proof = db.get(Evidence, evidence_id)
                child = db.get(Materialization, materialization_id)
                if proof is None and child is None:
                    now = utc(service.clock())
                    proof = Evidence(id=evidence_id, organization_id=original.organization_id,
                        source_id=original.source_id, source_version_id=version_id, locator=locator,
                        extractor=extractor, confidence=None, confidence_kind="unknown", extracted_at=now,
                        policy_pins=service.authority.policy.policy_pins())
                    db.add(proof); db.flush()
                    db.add(EvidenceAssessment(evidence_id=evidence_id, organization_id=original.organization_id,
                        verification="unverified", freshness="unknown", availability="unknown"))
                    db.flush()
                    audit(db, service.authority.policy, scope, object_ref(scope, "evidence", evidence_id),
                          "SOURCE_OBSERVED", now, _pin(scope, "evidence", evidence_id))
                    admitted = service.admit(db, scope=scope, evidence=_pin(scope, "evidence", evidence_id),
                        source_version=binding.source_version_pin, residency=original.residency,
                        retention_until=utc(original.retention_until), kek=KekRef(original.kek_reference, original.kek_version),
                        allow_copy=False, allow_derive=True, parent=service._pin(scope, original),
                        materialization_id=materialization_id, object_id=object_id)
                    service.begin_write(db, scope=scope, materialization=admitted, fence=new_fence())
                    child = db.get(Materialization, materialization_id, populate_existing=True)
                if (proof is None or child is None or proof.locator != locator or proof.extractor != extractor
                        or proof.source_id != original.source_id or proof.source_version_id != version_id
                        or proof.policy_pins != service.authority.policy.policy_pins()
                        or child.evidence_id != evidence_id or child.object_id != object_id
                        or child.parent_id != original_id or child.owner_id != record.scope.owner_id
                        or child.project_id != original.project_id or child.organization_id != original.organization_id
                        or child.source_id != original.source_id or child.source_version_id != version_id
                        or child.copy_allowed or not child.derive_allowed
                        or child.residency != original.residency or child.kek_reference != original.kek_reference
                        or child.kek_version != original.kek_version or utc(child.retention_until) != utc(original.retention_until)
                        or child.state not in {"WRITING", "SEALED", "DERIVED"}
                        or db.get(EvidenceAssessment, evidence_id) is None):
                    deny()
                states.append(child.state)
            if "DERIVED" in states and set(states) != {"DERIVED"}:
                deny()
            if set(states) == {"DERIVED"}:
                for evidence_id, _, _, _, _, _ in plans:
                    proof = db.get(Evidence, evidence_id)
                    require_local_xlsx_fragment(db, scope=scope,
                        source=db.get(SourceReference, original.source_id),
                        version=db.get(SourceVersion, version_id), evidence=proof,
                        descriptor=RepresentationDescriptor.model_validate(proof.representation_ref), clock=self.lifecycle.clock)
            # Durable registry and fences precede any child ciphertext effect.
            self._live(db, record)
            db.commit()
            if set(states) == {"DERIVED"}:
                return
            for evidence_id, materialization_id, _, _, data, _ in plans:
                service, scope, _, binding = self._live(db, record)
                child = db.get(Materialization, materialization_id, populate_existing=True)
                if child.state == "WRITING":
                    # The exact live job row remains locked; only this durable
                    # fence can own leftover partial writes from its earlier run.
                    self.lifecycle.storage.cleanup_partials(child.object_id,
                        eligible_fences={child.active_fence}, active_fences=set())
                    service.seal(db, scope=scope, materialization=service._pin(scope, child),
                        fence=child.active_fence, source=BytesIO(data), max_bytes=MAX_FRAGMENT_BYTES,
                        kind="quote", media_type="application/json; charset=utf-8")
            # No representation_ref becomes durable until every child sealed.
            for evidence_id, materialization_id, _, _, _, _ in plans:
                service, scope, _, binding = self._live(db, record)
                child = db.get(Materialization, materialization_id, populate_existing=True)
                service.derive(db, scope=scope, materialization=service._pin(scope, child))
                assessment = db.get(EvidenceAssessment, evidence_id)
                assessment.record_version += 1
                assessment.freshness, assessment.availability = "fresh", "available"
                assessment.checked_at, assessment.valid_until = utc(service.clock()), binding.valid_until
                # Fresh means this immutable original observation, not formula
                # recalculation. Human verification remains explicitly absent.
            self._live(db, record)
            db.commit()
        except Exception:
            db.rollback()
            raise SourceEvidenceError("xlsx_evidence_unavailable") from None

    def recover_retention(self, session_factory, *, limit):
        """Bounded parent-job recovery; no user mandate is minted for cleanup."""
        if type(limit) is not int or not 1 <= limit <= 500:
            deny()
        backend = self.lifecycle
        authority = backend.retention_authority
        if not isinstance(authority, LocalUploadRetentionAuthority):
            deny()
        now = utc(backend.clock())
        with session_factory() as db:
            candidates = list(db.execute(select(Materialization.id, Materialization.parent_id).where(
                Materialization.parent_id.is_not(None), Materialization.state != "PURGED",
                Materialization.retention_until <= now,
            ).order_by(Materialization.retention_until, Materialization.id).limit(limit * 4)))
        purged = 0
        for identity, parent_id in candidates:
            if purged >= limit:
                break
            try:
                with session_factory() as db:
                    # Retention capability locks the project before any parent
                    # or child row, matching the live reader/ingestion ordering.
                    candidate = db.get(Materialization, identity)
                    authority.require(db, candidate)
                    candidate_scope = (candidate.organization_id, candidate.project_id, candidate.owner_id)
                    original = db.scalar(select(Materialization).where(Materialization.id == parent_id)
                                         .with_for_update().execution_options(populate_existing=True))
                    child = db.scalar(select(Materialization).where(Materialization.id == identity)
                                      .with_for_update().execution_options(populate_existing=True))
                    proof = db.get(Evidence, child.evidence_id)
                    source = db.get(SourceReference, original.source_id)
                    version = db.get(SourceVersion, original.source_version_id)
                    root_proof = db.get(Evidence, original.evidence_id)
                    source_identity = db.get(ConnectionIdentity, source.identity_id) if source else None
                    observation = LocalUploadObservation.model_validate(version.locator_at_observation) if version else None
                    authority.require(db, child)
                    authority.require(db, original)
                    if ((child.organization_id, child.project_id, child.owner_id) != candidate_scope
                            or child.parent_id != original.id or original.parent_id is not None or child.source_id != original.source_id
                            or child.source_version_id != original.source_version_id or child.owner_id != original.owner_id
                            or child.organization_id != original.organization_id or child.project_id != original.project_id
                            or proof is None or proof.source_version_id != child.source_version_id
                            or proof.source_id != child.source_id or proof.organization_id != child.organization_id
                            or proof.extractor != {**_EXTRACTOR, "configuration_digest": _CONFIG_DIGEST}
                            or _ids(child.source_version_id, proof.locator) != (proof.id, child.id, child.object_id)
                            or source is None or source.namespace != "local-upload" or source.object_kind != "file"
                            or source.origin_project_id != original.project_id or source.organization_id != original.organization_id
                            or source.id != _stable_uuid("source", f"{original.organization_id}:{original.owner_id}:{original.project_id}:{source.external_id}")
                            or original.id != str(UUID(hex=source.external_id[:32]))
                            or version is None or version.id != _stable_uuid("version", source.id) or version.revision != 1
                            or version.observation_key != source.external_id or observation.staging_id != source.external_id[:32]
                            or root_proof is None or root_proof.id != _stable_uuid("evidence", source.id)
                            or root_proof.locator != {"kind": "whole_object", "reason_code": "local_upload"}
                            or root_proof.extractor != {"name": "local_upload", "version": "1"}
                            or source_identity is None or source_identity.provider != "local_upload"
                            or source_identity.id != _stable_uuid("identity", f"{original.organization_id}:{original.owner_id}")
                            or utc(child.retention_until) > now):
                        deny()
                    job = backend._job(db, original)
                    if job is None or job.status not in {"completed", "cancelled", "failed", "dead_letter"}:
                        deny()
                    if child.state != "EXPIRED":
                        # For a WRITING crash, retain the durable fence until
                        # both partial and complete ciphertext deletion succeeds.
                        if child.state == "WRITING":
                            backend.storage.cleanup_partials(child.object_id,
                                eligible_fences={child.active_fence}, active_fences=set())
                            backend.storage.delete(child.object_id)
                        next_version = child.record_version + 1
                        cas(db, Materialization, [Materialization.id == child.id,
                            Materialization.state == child.state], child.record_version,
                            state="EXPIRED", expired_at=now, active_fence=None)
                        child = db.get(Materialization, identity)
                        backend._service_audit(db, child, "MATERIALIZATION_EXPIRED", now, next_version)
                        db.commit()
                    # Repeatable delete precedes the final tombstone commit.
                    backend.storage.delete(child.object_id)
                    next_version = child.record_version + 1
                    cas(db, Materialization, [Materialization.id == child.id, Materialization.state == "EXPIRED"],
                        child.record_version, state="PURGED", purged_at=now, active_fence=None,
                        format_version=None, chunk_size=None, wrapped_dek=None,
                        manifest=PurgeTombstone(schema_version="v54.materialization.tombstone.1").model_dump(exclude_none=True))
                    child = db.get(Materialization, identity)
                    backend._service_audit(db, child, "MATERIALIZATION_PURGED", now, next_version)
                    db.commit()
                purged += 1
            except Exception:
                continue
        return purged


def require_local_xlsx_fragment(db, *, scope, source, version, evidence, descriptor, clock):
    """The local-only branch never waives a mailbox check for other providers."""
    for pending in set(db.new) | set(db.dirty) | set(db.deleted):
        if isinstance(pending, (Evidence, EvidenceAssessment, Materialization)):
            state = inspect(pending)
            key = "evidence_id" if isinstance(pending, EvidenceAssessment) else "id"
            target = descriptor.representation_id if isinstance(pending, Materialization) else evidence.id
            if state.dict.get(key, target) == target or state.identity == (target,):
                deny()
    binding = require_local_upload_source(db, scope=scope, source_ref=object_ref(scope, "source", source.id),
        source_version_pin=_pin(scope, "source_version", version.id), operation="fragment", lock=False,
        allow_purged_original=True, clock=clock)
    evidence = db.get(Evidence, evidence.id, populate_existing=True)
    db.get(EvidenceAssessment, evidence.id, populate_existing=True)
    child = db.get(Materialization, descriptor.representation_id, populate_existing=True)
    original = db.get(Materialization, binding.original_materialization_id)
    extractor = ExtractorMetadata.model_validate(evidence.extractor)
    if (RepresentationDescriptor.model_validate(evidence.representation_ref) != descriptor
            or child is None or child.state != "DERIVED" or child.parent_id != original.id
            or child.evidence_id != evidence.id or child.owner_id != binding.owner_id
            or child.organization_id != evidence.organization_id or child.project_id != source.origin_project_id
            or child.source_id != source.id or child.source_version_id != version.id
            or child.object_id != descriptor.handle or child.copy_allowed or not child.derive_allowed
            or child.residency != original.residency or child.kek_reference != original.kek_reference
            or child.kek_version != original.kek_version or utc(child.retention_until) != binding.retention_until
            or descriptor.kind != "quote" or descriptor.media_type != "application/json; charset=utf-8"
            or utc(descriptor.expires_at) != utc(child.retention_until)
            or _ids(version.id, evidence.locator) != (evidence.id, child.id, child.object_id)
            or extractor.name != _EXTRACTOR["name"] or extractor.version != _EXTRACTOR["version"]
            or evidence.extractor != {**_EXTRACTOR, "configuration_digest": _CONFIG_DIGEST}):
        deny()
    manifest = MaterializationManifest.model_validate(child.manifest)
    if (manifest.evidence_pin != descriptor.evidence_pin or manifest.source_ref != descriptor.source_ref
            or manifest.source_version_pin != descriptor.source_version_pin
            or manifest.kind != descriptor.kind or manifest.media_type != descriptor.media_type
            or manifest.storage.object_id != child.object_id or manifest.storage.wrapped_dek != child.wrapped_dek
            or manifest.storage.kek_reference != child.kek_reference or manifest.storage.kek_version != child.kek_version
            or manifest.storage.format_version != child.format_version or manifest.storage.chunk_size != child.chunk_size):
        deny()
    return binding


def validate_local_xlsx_payload(data, *, evidence, binding):
    payload = XlsxCellSnapshot.model_validate_json(data)
    if (payload.original_sha256 != binding.checksum_sha256
            or payload.locator.model_dump(mode="json") != evidence.locator
            or payload.locator.value_kind not in {"formula", "cached_value"}
            or payload.locator.value_kind == "cached_value" and payload.cache_state != "present"):
        deny()
