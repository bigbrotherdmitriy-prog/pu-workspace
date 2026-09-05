"""Strict server-owned HTTP projection for readable evidence fragments."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import TypeAdapter
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.v54_pilot import (
    ConnectionIdentity,
    Evidence,
    EvidenceAssessment,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)
from app.source_evidence.fragment_reader import EvidenceLocator, FragmentReadResult


SCHEMA_VERSION = "evidence-fragment.v54.2"
_LOCATOR = TypeAdapter(EvidenceLocator)


def unavailable_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "state": "unavailable",
        "status": "unavailable",
        "reason_code": "resource_unavailable",
    }


def locator_payload(value) -> dict:
    return _LOCATOR.validate_python(value).model_dump(mode="json")


def _timestamp(value: datetime) -> str:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def readable_payload(db: Session, result: FragmentReadResult) -> dict:
    row = db.execute(
        select(
            Evidence,
            EvidenceAssessment,
            SourceReference,
            SourceVersion,
            SourceCurrent,
            ConnectionIdentity,
            Project,
        )
        .select_from(Evidence)
        .join(EvidenceAssessment, and_(
            EvidenceAssessment.evidence_id == Evidence.id,
            EvidenceAssessment.organization_id == Evidence.organization_id,
        ))
        .join(SourceReference, and_(
            SourceReference.id == Evidence.source_id,
            SourceReference.organization_id == Evidence.organization_id,
        ))
        .join(SourceVersion, and_(
            SourceVersion.id == Evidence.source_version_id,
            SourceVersion.source_id == Evidence.source_id,
            SourceVersion.organization_id == Evidence.organization_id,
        ))
        .join(SourceCurrent, and_(
            SourceCurrent.source_id == Evidence.source_id,
            SourceCurrent.organization_id == Evidence.organization_id,
        ))
        .join(ConnectionIdentity, and_(
            ConnectionIdentity.id == SourceReference.identity_id,
            ConnectionIdentity.organization_id == Evidence.organization_id,
        ))
        .join(Project, and_(
            Project.id == SourceReference.origin_project_id,
            Project.organization_id == Evidence.organization_id,
        ))
        .where(
            Evidence.id == result.evidence_pin.ref.id.value,
            Evidence.organization_id == int(result.evidence_pin.ref.tenant_id.value),
            Evidence.revision == result.evidence_pin.value,
            SourceReference.id == result.source_ref.id.value,
            SourceVersion.id == result.source_version_pin.ref.id.value,
        )
    ).one_or_none()
    if row is None:
        raise ValueError("resource_unavailable")
    evidence, assessment, source, version, current, identity, project = row
    extractor = result.extractor
    status = result.effective_status
    reviewer = f"user-{assessment.reviewed_by}" if status == "verified" else None
    reviewed_at = _timestamp(assessment.reviewed_at) if status == "verified" else None
    return {
        "schema_version": SCHEMA_VERSION,
        "state": "readable",
        "status": status,
        "version_state": result.version_state,
        "freshness": result.freshness,
        "availability": result.availability,
        "valid_until": _timestamp(result.valid_until),
        "evidence": {
            "id": evidence.id,
            "revision": evidence.revision,
            "source_id": source.id,
            "source_version_id": version.id,
        },
        "source": {
            "id": source.id,
            "record_version": source.record_version,
            "current_source_version_id": current.version_id,
            "provider": identity.provider,
            "account": identity.id,
            "namespace": source.namespace,
            "origin_project": project.name,
        },
        "source_version": {
            "id": version.id,
            "revision": version.revision,
            "source_id": source.id,
        },
        "locator": locator_payload(result.locator),
        "fragment": {"media_type": result.media_type, "excerpt": result.fragment},
        "extracted_fact": None,
        "ai_conclusion": None,
        "extractor": {
            "name": extractor.name,
            "version": extractor.version,
            "method": extractor.method,
            "model_provider": extractor.model_provider,
            "model_id": extractor.model_id,
            "model_version": extractor.model_version,
            "prompt_version": extractor.prompt_version,
        },
        "confidence": {
            "value": result.confidence,
            "kind": result.confidence_kind,
            "calibration_ref": None,
        },
        "assessment": {
            "verification": status,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "record_version": result.assessment_record_version,
        },
    }
