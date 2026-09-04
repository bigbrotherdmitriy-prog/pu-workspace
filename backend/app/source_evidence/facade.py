"""Synthetic metadata facades. All writes join the caller transaction."""
from uuid import UUID

from app.core.v54_interfaces import Resolution, ReviewCommand, require_resolution
from app.core.v54_permissions import (
    SourceEvidenceError, boundary, check_pin, check_ref, deny, identifier, load,
    object_ref, positive, utc, utcnow,
)
from app.core.v54_refs import VersionPin
from app.models.user import User
from app.models.v54_pilot import (
    ConnectionIdentity, MailConnection, SourceReference, SourceVersion, SourceCurrent,
    Evidence, EvidenceAssessment,
)
from app.source_evidence.common import audit, cas


def pin(scope, kind, identity, value=1):
    return VersionPin(ref=object_ref(scope, kind, identity),
                      version_kind="record_version" if kind == "source" else "revision", value=value)


class SourceEvidenceFacade:
    def __init__(self, policy, clock=utcnow):
        self.policy, self.clock = policy, clock

    def _require(self, db, scope, operation, now, lock=False):
        return self.policy.require(db, scope, operation, now, lock=lock)

    def _identity(self, db, scope, identity, namespace, lock=False):
        check_ref(scope, identity, "connection_identity")
        row = load(db, ConnectionIdentity, ConnectionIdentity.id == identity.id.value,
                   ConnectionIdentity.organization_id == self.policy.tenant_id, lock=lock)
        self.policy.identity(row, namespace)
        # MailConnection is read-only here; its sole writer is Communication.
        mailbox = load(db, MailConnection, MailConnection.identity_id == row.id,
                       MailConnection.organization_id == self.policy.tenant_id,
                       MailConnection.namespace == namespace, lock=lock)
        if not mailbox or mailbox.state != "active":
            deny()
        return row

    def _source(self, db, scope, source_ref, operation, now, lock=False):
        self._require(db, scope, operation, now, lock)
        check_ref(scope, source_ref, "source")
        row = load(db, SourceReference, SourceReference.id == source_ref.id.value,
                   SourceReference.organization_id == self.policy.tenant_id,
                   SourceReference.origin_project_id == self.policy.project_id)
        if not row:
            deny()
        identity = self._identity(db, scope, object_ref(scope, "connection_identity", row.identity_id),
                                  row.namespace, lock)
        row = load(db, SourceReference, SourceReference.id == row.id,
                   SourceReference.organization_id == self.policy.tenant_id, lock=lock)
        if (row.origin_project_id != self.policy.project_id
                or row.policy_pins != self.policy.policy_pins()
                or row.residency != {"source_location": "synthetic", "assurance": "test_only"}):
            deny()
        return row, identity

    def _chain(self, db, scope, requested, operation, now, lock=False):
        self._require(db, scope, operation, now, lock)
        evidence = None
        assessment = None
        if requested.ref.type == "evidence":
            check_pin(scope, requested, "evidence")
            evidence = load(db, Evidence, Evidence.id == requested.ref.id.value,
                            Evidence.organization_id == self.policy.tenant_id)
            if not evidence:
                deny()
            source_ref = object_ref(scope, "source", evidence.source_id)
            version_id = evidence.source_version_id
        elif requested.ref.type == "source_version":
            check_pin(scope, requested, "source_version")
            version = load(db, SourceVersion, SourceVersion.id == requested.ref.id.value,
                           SourceVersion.organization_id == self.policy.tenant_id)
            if not version:
                deny()
            source_ref, version_id = object_ref(scope, "source", version.source_id), version.id
        elif requested.ref.type == "source":
            check_pin(scope, requested, "source")
            source_ref, version_id = requested.ref, None
        else:
            deny()
        source, identity = self._source(db, scope, source_ref, operation, now, lock)
        current = load(db, SourceCurrent, SourceCurrent.source_id == source.id,
                       SourceCurrent.organization_id == self.policy.tenant_id, lock=lock)
        if not current:
            deny()
        version = load(db, SourceVersion, SourceVersion.id == (version_id or current.version_id),
                       SourceVersion.organization_id == self.policy.tenant_id,
                       SourceVersion.source_id == source.id, lock=lock)
        if not version:
            deny()
        if requested.ref.type == "source_version" and requested.value != version.revision:
            deny()
        if evidence:
            evidence = load(db, Evidence, Evidence.id == evidence.id,
                            Evidence.organization_id == self.policy.tenant_id, lock=lock)
            assessment = load(db, EvidenceAssessment, EvidenceAssessment.evidence_id == evidence.id,
                              EvidenceAssessment.organization_id == self.policy.tenant_id, lock=lock)
            if not assessment or evidence.policy_pins != self.policy.policy_pins():
                deny()
            if requested.value != evidence.revision:
                deny()
        return source, identity, version, current, evidence, assessment

    @boundary
    def register_source(self, db, *, scope, identity, namespace, external_id, object_kind,
                        parent=None, incarnation=1):
        now = self.clock()
        self._require(db, scope, "write", now, True)
        self._require(db, scope, "audit", now)
        identifier(namespace)
        identifier(external_id)
        positive(incarnation)
        if object_kind not in {"message", "attachment"} or (object_kind == "attachment") != (parent is not None):
            deny()
        account = self._identity(db, scope, identity, namespace, True)
        if parent is not None:
            parent_row, _ = self._source(db, scope, parent, "write", now, True)
            if parent_row.identity_id != account.id or parent_row.namespace != namespace or parent_row.object_kind != "message":
                deny()
        row = load(db, SourceReference, SourceReference.identity_id == account.id,
                   SourceReference.organization_id == self.policy.tenant_id,
                   SourceReference.namespace == namespace, SourceReference.external_id == external_id,
                   SourceReference.incarnation == incarnation, lock=True)
        if row:
            if (row.origin_project_id != self.policy.project_id or row.object_kind != object_kind
                    or row.parent_source_id != (parent.id.value if parent else None)
                    or row.policy_pins != self.policy.policy_pins()):
                deny()
            return pin(scope, "source", row.id, row.record_version)
        row = SourceReference(organization_id=self.policy.tenant_id, origin_project_id=self.policy.project_id,
                              identity_id=account.id, namespace=namespace, external_id=external_id,
                              incarnation=incarnation, external_id_kind="stable_id", object_kind=object_kind,
                              parent_source_id=parent.id.value if parent else None,
                              canonical_locator={"kind": "opaque_id", "value": external_id, "normalization_version": "1"},
                              policy_pins=self.policy.policy_pins(),
                              residency={"source_location": "synthetic", "assurance": "test_only"})
        db.add(row)
        db.flush()
        result = pin(scope, "source", row.id, row.record_version)
        audit(db, self.policy, scope, result.ref, "SOURCE_OBSERVED", now, result)
        return result

    @boundary
    def observe(self, db, *, scope, source, identity, namespace, observation_key, provider_revision):
        now = self.clock()
        check_pin(scope, source, "source")
        row, account = self._source(db, scope, source.ref, "observe", now, True)
        self._require(db, scope, "audit", now)
        check_ref(scope, identity, "connection_identity")
        if account.id != identity.id.value or namespace != row.namespace:
            deny()
        identifier(observation_key, 200)
        if provider_revision is not None:
            identifier(provider_revision, 500)
        previous = load(db, SourceVersion, SourceVersion.source_id == row.id,
                        SourceVersion.organization_id == self.policy.tenant_id,
                        SourceVersion.observation_key == observation_key)
        if previous:
            current = load(db, SourceCurrent, SourceCurrent.source_id == row.id)
            if (previous.provider_revision != provider_revision or not current or current.version_id != previous.id):
                raise SourceEvidenceError("version_conflict")
            # Exact duplicate delivery does not advance current, freshness or audit.
            return pin(scope, "source", row.id, row.record_version), pin(scope, "source_version", previous.id)
        deadline = self._require(db, scope, "observe", now)
        row_id, locator = row.id, dict(row.canonical_locator)
        cas(db, SourceReference, [SourceReference.id == row_id, SourceReference.organization_id == self.policy.tenant_id],
            source.value, freshness="fresh" if provider_revision is not None else "unknown",
            availability="available", sync_state="current", last_seen_at=now, last_checked_at=now, next_check_at=deadline)
        version = SourceVersion(organization_id=self.policy.tenant_id, source_id=row_id,
                                observation_key=observation_key, provider_revision=provider_revision,
                                consistency="revision_bound" if provider_revision is not None else "unknown",
                                locator_at_observation=locator, integrity=[], observed_at=now)
        db.add(version)
        db.flush()
        current = load(db, SourceCurrent, SourceCurrent.source_id == row_id,
                       SourceCurrent.organization_id == self.policy.tenant_id, lock=True)
        if current:
            current.version_id = version.id
        else:
            db.add(SourceCurrent(source_id=row_id, organization_id=self.policy.tenant_id, version_id=version.id))
        db.flush()
        new_source, observation = pin(scope, "source", row_id, source.value + 1), pin(scope, "source_version", version.id)
        audit(db, self.policy, scope, new_source.ref, "SOURCE_OBSERVED", now, new_source)
        return new_source, observation

    @boundary
    def mark_unavailable(self, db, *, scope, source, availability):
        now = self.clock()
        check_pin(scope, source, "source")
        row, _ = self._source(db, scope, source.ref, "observe", now, True)
        self._require(db, scope, "audit", now)
        if availability not in {"access_denied", "provider_unavailable", "deleted", "unknown"}:
            deny()
        cas(db, SourceReference, [SourceReference.id == row.id, SourceReference.organization_id == self.policy.tenant_id],
            source.value, availability=availability, freshness="unknown", sync_state="degraded",
            last_checked_at=now, next_check_at=None)
        result = pin(scope, "source", source.ref.id.value, source.value + 1)
        audit(db, self.policy, scope, result.ref, "BLOCKED", now, result)
        return result

    @boundary
    def create_evidence(self, db, *, scope, source, version, evidence_id):
        now = self.clock()
        check_ref(scope, source, "source")
        check_pin(scope, version, "source_version")
        if type(evidence_id) is not str or str(UUID(evidence_id)) != evidence_id:
            deny()
        row, _, observation, current, _, _ = self._chain(db, scope, version, "write", now, True)
        self._require(db, scope, "audit", now)
        if row.id != source.id.value or current.version_id != observation.id or row.availability != "available":
            deny()
        prior = load(db, Evidence, Evidence.id == evidence_id, lock=True)
        if prior:
            if (prior.organization_id != self.policy.tenant_id or prior.source_id != row.id
                    or prior.source_version_id != observation.id or prior.policy_pins != self.policy.policy_pins()):
                deny()
            return pin(scope, "evidence", prior.id)
        evidence = Evidence(id=evidence_id, organization_id=self.policy.tenant_id, source_id=row.id,
                            source_version_id=observation.id,
                            locator={"kind": "whole_object", "reason_code": "synthetic_fixture"},
                            extractor={"name": "fixture", "version": "1"}, confidence=None,
                            confidence_kind="unknown", extracted_at=now, policy_pins=self.policy.policy_pins())
        db.add(evidence)
        db.flush()
        db.add(EvidenceAssessment(evidence_id=evidence.id, organization_id=self.policy.tenant_id,
                                  freshness=row.freshness, availability=row.availability,
                                  checked_at=now, valid_until=row.next_check_at))
        db.flush()
        result = pin(scope, "evidence", evidence.id)
        audit(db, self.policy, scope, result.ref, "SOURCE_OBSERVED", now, result)
        return result

    @boundary
    def resolve(self, db, *, scope, pin, operation, lock=False):
        if operation not in {"metadata", "fragment", "review", "dispatch"}:
            deny()
        result = Resolution(pin=pin, actor=scope.actor, project=scope.project, operation=operation)
        # Fragment bytes remain inaccessible without the separately injected,
        # request-bound materialization store and an explicit fragment grant.
        now = self.clock()
        try:
            source, identity, version, current, evidence, assessment = self._chain(db, scope, pin, operation, now, lock)
            deadline = self._require(db, scope, operation, now)
            version_ok = current.version_id == version.id
            if pin.ref.type == "source":
                version_ok = version_ok and source.record_version == pin.value
            consistency = version.consistency == "revision_bound" and bool(version.provider_revision)
            times = [utc(source.next_check_at), deadline]
            fresh = source.freshness == "fresh" and utc(source.last_checked_at) is not None and utc(source.last_checked_at) <= now
            if source.last_checked_at is not None:
                times.append(utc(source.last_checked_at) + self.policy.freshness_ttl)
            available = source.availability == "available"
            verified = False
            if assessment:
                times.append(utc(assessment.valid_until))
                fresh = fresh and assessment.freshness == "fresh" and utc(assessment.checked_at) is not None and utc(assessment.checked_at) <= now
                available = available and assessment.availability == "available"
                if assessment.checked_at is not None:
                    times.append(utc(assessment.checked_at) + self.policy.freshness_ttl)
                verified = (assessment.verification == "verified"
                            and assessment.reviewed_by is not None
                            and self.policy.permits(db, scope, assessment.reviewed_by, "review", now, lock=lock)
                            and utc(assessment.reviewed_at) is not None
                            and utc(assessment.reviewed_at) <= now
                            and db.get(User, assessment.reviewed_by) is not None)
            expires = min(times) if all(t is not None for t in times) else None
            fresh = fresh and expires is not None and expires > now
            return result.model_copy(update={
                "acl": "allow", "version": "current" if version_ok and consistency else "changed" if not version_ok else "unknown",
                "freshness": "fresh" if fresh else "stale", "availability": "available" if available else "unavailable",
                "verification": "verified" if verified else "unverified", "policy_known": True,
                "retention_known": True, "residency_allowed": True, "valid_until": expires,
                "authority_epoch": self.policy.resolved_authority_epoch(db, scope, operation, now, lock=lock),
                "binding_epoch": identity.binding_epoch,
            })
        except SourceEvidenceError:
            return result.model_copy(update={"acl": "deny"})

    @boundary
    def check_evidence_before_dispatch(self, db, *, scope, evidence, lock=True):
        check_pin(scope, evidence, "evidence")
        result = self.resolve(db, scope=scope, pin=evidence, operation="dispatch", lock=lock)
        require_resolution(result, scope=scope, pin=evidence, operation="dispatch", now=self.clock())
        return result

    @boundary
    def review(self, db, *, scope, command: ReviewCommand):
        now = self.clock()
        check_pin(scope, command.subject, "evidence")
        result = self.resolve(db, scope=scope, pin=command.subject, operation="review", lock=True)
        require_resolution(result, scope=scope, pin=command.subject, operation="review", now=now)
        self._require(db, scope, "audit", now)
        cas(db, EvidenceAssessment, [EvidenceAssessment.evidence_id == command.subject.ref.id.value,
            EvidenceAssessment.organization_id == self.policy.tenant_id], command.expected_record_version,
            verification="verified" if command.decision == "confirmed" else "unverified",
            reviewed_by=int(scope.actor.id.value), reviewed_at=now)
        audit(db, self.policy, scope, command.subject.ref, "EVIDENCE_REVIEWED", now, command.subject)

    @boundary
    def recheck_evidence(self, db, *, scope, evidence, expected_assessment_version,
                         expected_source_version, observed_provider_revision):
        now = self.clock()
        check_pin(scope, evidence, "evidence")
        positive(expected_assessment_version)
        positive(expected_source_version)
        source, _, version, current, _, assessment = self._chain(db, scope, evidence, "observe", now, True)
        deadline = self._require(db, scope, "observe", now)
        self._require(db, scope, "audit", now)
        if (version.consistency != "revision_bound" or not version.provider_revision
                or observed_provider_revision != version.provider_revision or version.id != current.version_id
                or source.availability != "available" or assessment.availability != "available"
                or source.record_version != expected_source_version or assessment.record_version != expected_assessment_version):
            deny()
        cas(db, SourceReference, [SourceReference.id == source.id, SourceReference.organization_id == self.policy.tenant_id],
            expected_source_version, freshness="fresh", last_seen_at=now, last_checked_at=now, next_check_at=deadline)
        cas(db, EvidenceAssessment, [EvidenceAssessment.evidence_id == evidence.ref.id.value,
            EvidenceAssessment.organization_id == self.policy.tenant_id], expected_assessment_version,
            freshness="fresh", checked_at=now, valid_until=deadline)
        audit(db, self.policy, scope, evidence.ref, "SOURCE_OBSERVED", now, evidence)
        return evidence
