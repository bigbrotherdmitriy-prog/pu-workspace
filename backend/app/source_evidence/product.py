"""Production read composition for exact evidence fragments.

There is deliberately no built-in materialization store yet.  Deployments may
install a narrow ``FragmentStore`` adapter on ``app.state.v54_fragment_store``;
absence or malformed adapters fail closed before any content is returned.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.v54_authority import AuthorityDenied, AuthorityResolver
from app.core.v54_interfaces import RequestScope, Resolution
from app.core.v54_permissions import SourceEvidenceError, utc
from app.models.v54_pilot import (
    ConnectionIdentity,
    Evidence,
    EvidenceAssessment,
    SourceCurrent,
    SourceReference,
)
from app.source_evidence.fragment_reader import FragmentStore, RepresentationDescriptor


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def unavailable() -> None:
    raise SourceEvidenceError("resource_unavailable")


class UnavailableFragmentStore:
    def read(self, request):
        unavailable()


def fragment_store_from_app(request: Request) -> FragmentStore:
    store = getattr(request.app.state, "v54_fragment_store", None)
    if store is None or not callable(getattr(store, "read", None)):
        return UnavailableFragmentStore()
    return store


class ProductEvidenceResolver:
    """Re-evaluate live DB authority and policy facts for a fragment read."""

    def __init__(self, *, clock=utcnow):
        self.clock = clock
        self.authority = AuthorityResolver(clock=clock)

    def resolve(
        self,
        db: Session,
        *,
        scope: RequestScope,
        pin,
        operation: str,
        lock: bool,
    ) -> Resolution:
        if operation != "fragment" or lock or not db.in_transaction():
            unavailable()
        now = utc(self.clock())
        try:
            mandate = self.authority.require(db, scope, "fragment", now, lock=False)
            row = db.execute(
                select(
                    Evidence,
                    EvidenceAssessment,
                    SourceReference,
                    SourceCurrent,
                    ConnectionIdentity,
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
                .join(SourceCurrent, and_(
                    SourceCurrent.source_id == Evidence.source_id,
                    SourceCurrent.organization_id == Evidence.organization_id,
                ))
                .join(ConnectionIdentity, and_(
                    ConnectionIdentity.id == SourceReference.identity_id,
                    ConnectionIdentity.organization_id == Evidence.organization_id,
                ))
                .where(
                    Evidence.id == pin.ref.id.value,
                    Evidence.organization_id == int(scope.tenant.value),
                    Evidence.revision == pin.value,
                    SourceReference.origin_project_id == int(scope.project.id.value),
                )
            ).one_or_none()
            if row is None:
                unavailable()
            evidence, assessment, source, current, identity = row
            descriptor = RepresentationDescriptor.model_validate(evidence.representation_ref)
            descriptor_expiry = utc(descriptor.expires_at)
            assessment_expiry = utc(assessment.valid_until)
            source_expiry = utc(source.next_check_at)
            policy_known = (
                isinstance(source.policy_pins, dict)
                and set(source.policy_pins) == {"access", "retention", "residency"}
                and evidence.policy_pins == source.policy_pins
            )
            residency_allowed = isinstance(source.residency, dict) and bool(source.residency)
            if (
                now is None
                or descriptor.retention_state != "active"
                or descriptor_expiry is None
                or assessment_expiry is None
                or source_expiry is None
                or min(descriptor_expiry, assessment_expiry, source_expiry, mandate.valid_until) <= now
                or not policy_known
                or not residency_allowed
            ):
                unavailable()
            version = "current" if current.version_id == evidence.source_version_id else "historical"
            return Resolution(
                pin=pin,
                actor=scope.actor,
                project=scope.project,
                operation="fragment",
                acl="allow",
                version=version,
                freshness="fresh" if source.freshness == assessment.freshness == "fresh" else "stale",
                availability=(
                    "available"
                    if source.availability == assessment.availability == "available"
                    else "unavailable"
                ),
                verification=assessment.verification,
                policy_known=True,
                retention_known=True,
                residency_allowed=True,
                valid_until=min(
                    descriptor_expiry,
                    assessment_expiry,
                    source_expiry,
                    mandate.valid_until,
                ),
                authority_epoch=mandate.authority_epoch,
                binding_epoch=identity.binding_epoch,
            )
        except (AuthorityDenied, SourceEvidenceError, ValidationError, TypeError, ValueError):
            unavailable()
