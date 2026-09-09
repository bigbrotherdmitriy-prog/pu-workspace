"""Read-only identity adapter over existing encrypted Materialization rows.

This is not an export producer or a new cache. Only an immutable, revision-bound
SourceVersion with explicitly described export integrity is eligible. Legacy
modified-time/digest observations are deliberately not promoted to this contract.
No miss, stale entry or integrity error can initiate a provider request.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Literal

from pydantic import StrictInt, StrictStr, model_validator

from app.core.v54_permissions import SourceEvidenceError, deny, identifier, load, utc
from app.core.v54_refs import StrictDTO, VersionPin
from app.models.materialization import Materialization
from app.models.v54_pilot import ConnectionIdentity, Evidence, SourceCurrent, SourceReference, SourceVersion
from app.staging.lifecycle import MaterializationLifecycle, MaterializationManifest


class NativeExportObservation(StrictDTO):
    """Metadata in SourceVersion.locator_at_observation, written by its owner.

    integrity SHA256 describes the exported bytes, not the native original.
    Parsing this shape is not provider proof; no HTTP request accepts it here.
    """
    schema_version: Literal["native-export.observation.1"]
    original_mime_type: Literal[
        "application/vnd.google-apps.document", "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
    ]
    export_mime_type: StrictStr
    export_size: StrictInt

    @model_validator(mode="after")
    def bounded(self):
        # Only existing source_object materialization formats. No conversion here.
        if (self.export_mime_type not in {
                "text/plain", "text/csv", "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
                or not 0 < self.export_size <= 32 * 1024 * 1024):
            raise ValueError("resource_unavailable")
        return self


@dataclass(frozen=True)
class NativeExportManifest:
    """Projection only: the authoritative rows remain SourceVersion/Materialization."""
    original_id: str
    original_revision: str
    original_mime_type: str
    export_mime_type: str
    export_sha256: str
    export_size: int
    expires_at: datetime
    cache_key: str
    materialization: MaterializationManifest


@dataclass(frozen=True)
class NativeExportResult:
    manifest: NativeExportManifest
    content: bytes


class NativeExportCache:
    def __init__(self, lifecycle: MaterializationLifecycle):
        self.lifecycle = lifecycle

    def read(self, *, db, scope, materialization: VersionPin, source_version: VersionPin,
             original_id: str, original_revision: str, export_mime_type: str,
             max_bytes: int) -> NativeExportResult:
        """Exact-only read. All supplied identities are expectations, never proofs.

        Caller owns the transaction; this adapter performs no writes/commits or
        export fallback. Existing copy authorization runs before any cache bytes.
        """
        try:
            return self._read(db=db, scope=scope, materialization=materialization,
                source_version=source_version, original_id=original_id,
                original_revision=original_revision, export_mime_type=export_mime_type,
                max_bytes=max_bytes)
        except Exception:
            raise SourceEvidenceError("resource_unavailable") from None

    def _read(self, *, db, scope, materialization, source_version, original_id,
              original_revision, export_mime_type, max_bytes):
        identifier(original_id)
        identifier(original_revision, 500)
        if type(max_bytes) is not int or not 0 < max_bytes <= 32 * 1024 * 1024:
            deny()
        descriptor = self.lifecycle.authorize_read(db, scope=scope,
            materialization=materialization, max_bytes=max_bytes, for_copy=True)
        now = utc(self.lifecycle.clock())
        policy = self.lifecycle.authority.policy
        row = load(db, Materialization, Materialization.id == materialization.ref.id.value, lock=True)
        manifest = MaterializationManifest.model_validate(row.manifest)
        if (manifest.kind != "source_object" or manifest.source_version_pin != source_version
                or manifest.source_ref.tenant_id != scope.tenant):
            deny()
        version = load(db, SourceVersion, SourceVersion.id == row.source_version_id,
                       SourceVersion.organization_id == row.organization_id)
        source = load(db, SourceReference, SourceReference.id == row.source_id,
                      SourceReference.organization_id == row.organization_id, lock=True)
        current = load(db, SourceCurrent, SourceCurrent.source_id == row.source_id,
                       SourceCurrent.organization_id == row.organization_id, lock=True)
        evidence = load(db, Evidence, Evidence.id == row.evidence_id,
                        Evidence.organization_id == row.organization_id)
        if (not source or not version or not current or not evidence
                or version.source_id != source.id or source.origin_project_id != row.project_id
                or manifest.source_version_pin.ref.id.value != version.id
                or current.version_id != version.id or version.consistency != "revision_bound"
                or version.provider_revision != original_revision or source.external_id != original_id
                or source.external_id_kind != "opaque"
                or source.object_kind != "file" or source.availability != "available"
                or source.freshness != "fresh" or source.sync_state != "current"
                or source.next_check_at is None or utc(source.next_check_at) <= now
                or source.policy_pins != policy.policy_pins() or evidence.policy_pins != policy.policy_pins()
                or evidence.source_id != source.id or evidence.source_version_id != version.id
                or manifest.source_ref.id.value != source.id
                or manifest.evidence_pin.ref.id.value != evidence.id):
            deny()
        identity = load(db, ConnectionIdentity, ConnectionIdentity.id == source.identity_id,
                        ConnectionIdentity.organization_id == row.organization_id, lock=True)
        if (not identity or identity.provider != "google_drive" or identity.state != "verified"
                or identity.verified_at is None or identity.credential_generation <= 0
                or dict(policy.binding_epochs).get(identity.id) != identity.binding_epoch):
            deny()
        policy.account(identity.account_key, source.namespace)
        observation = NativeExportObservation.model_validate(version.locator_at_observation)
        integrity = version.integrity
        if (observation.export_mime_type != export_mime_type or manifest.media_type != export_mime_type
                or observation.export_size > max_bytes or not isinstance(integrity, list)
                or len(integrity) != 1 or set(integrity[0]) != {"algorithm", "value"}
                or integrity[0]["algorithm"] != "sha256"
                or not isinstance(integrity[0]["value"], str)
                or not re.fullmatch("[0-9a-f]{64}", integrity[0]["value"])):
            deny()
        digest = integrity[0]["value"]
        # Scope, source version, original identity and export format/hash all bind
        # the key. TTL is not refreshed and neither Evidence nor rows are created.
        key = sha256(json.dumps(["native-export.cache.1", row.organization_id, row.project_id,
            row.owner_id, source.id, version.id, original_id, original_revision,
            observation.original_mime_type, export_mime_type, digest],
            separators=(",", ":")).encode("utf-8")).hexdigest()
        output_manifest = NativeExportManifest(original_id, original_revision,
            observation.original_mime_type, export_mime_type, digest, observation.export_size,
            utc(row.retention_until), key, manifest)
        content = b"".join(self.lifecycle.storage.read_chunks(descriptor, max_bytes=max_bytes))
        if len(content) != observation.export_size or sha256(content).hexdigest() != digest:
            deny()
        # A read may span the retention boundary; never return expired bytes.
        if utc(self.lifecycle.clock()) >= min(output_manifest.expires_at, utc(source.next_check_at)):
            deny()
        if self.lifecycle.authorize_read(db, scope=scope, materialization=materialization,
                max_bytes=max_bytes, for_copy=True) != descriptor:
            deny()
        return NativeExportResult(output_manifest, content)
