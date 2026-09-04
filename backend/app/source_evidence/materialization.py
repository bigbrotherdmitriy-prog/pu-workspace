"""Exact Source/Evidence adapter for durable encrypted materializations."""
from __future__ import annotations

from sqlalchemy import select

from app.core.v54_permissions import SourceEvidenceError, deny
from app.core.v54_refs import VersionPin
from app.models.materialization import Materialization
from app.source_evidence.fragment_reader import FragmentStorePayload, FragmentStoreRequest
from app.staging.lifecycle import MaterializationLifecycle, MaterializationManifest


class MaterializedFragmentStore:
    """Request-bound adapter; it carries no global tenant or filesystem authority."""
    def __init__(self, db, *, scope, lifecycle: MaterializationLifecycle):
        self.db, self.scope, self.lifecycle = db, scope, lifecycle

    def read(self, request: FragmentStoreRequest) -> FragmentStorePayload:
        try:
            if not isinstance(request, FragmentStoreRequest):
                request = FragmentStoreRequest.model_validate(request)
            row = self.db.scalar(select(Materialization).where(
                Materialization.id == request.representation_id,
                Materialization.object_id == request.handle,
                Materialization.organization_id == int(self.scope.tenant.value),
                Materialization.project_id == int(self.scope.project.id.value),
                Materialization.owner_id == int(self.scope.actor.id.value),
                Materialization.state == "DERIVED",
            ))
            if not row:
                deny()
            manifest = MaterializationManifest.model_validate(row.manifest)
            if (manifest.evidence_pin != request.evidence_pin
                    or manifest.source_ref != request.source_ref
                    or manifest.source_version_pin != request.source_version_pin
                    or manifest.kind != request.kind or manifest.media_type != request.media_type):
                deny()
            pin = VersionPin(
                ref={"namespace": "pu", "type": "materialization",
                     "tenant_id": self.scope.tenant.model_dump(mode="json"),
                     "id": {"kind": "uuid", "value": row.id}},
                version_kind="record_version", value=row.record_version,
            )
            content = self.lifecycle.read(
                self.db, scope=self.scope, materialization=pin, max_bytes=request.max_bytes,
            )
            return FragmentStorePayload(
                representation_id=request.representation_id,
                evidence_pin=request.evidence_pin, source_ref=request.source_ref,
                source_version_pin=request.source_version_pin, kind=request.kind,
                media_type=request.media_type, fragment=content,
            )
        except SourceEvidenceError:
            raise
        except Exception:
            raise SourceEvidenceError("resource_unavailable") from None
