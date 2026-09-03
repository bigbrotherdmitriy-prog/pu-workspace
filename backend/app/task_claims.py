"""Task-owned deadline assertions. Never creates Task, approval or queue job."""
from datetime import date

from sqlalchemy import select

from app.action_trust.guards import Guards, TrustConflict, revision, sequence
from app.core.v54_dto import DeadlineClaimInput, canonical_json
from app.core.v54_interfaces import AuditAppend, RequestScope, ReviewCommand
from app.core.v54_refs import VersionPin, require_same_tenant
from app.core import v54_transactions
from app.models.ai_secretary import Message
from app.models.v54_pilot import DeadlineClaim


class DeadlineClaims:
    def __init__(self, *, guards: Guards):
        self.guards = guards

    def _message(self, db, scope, message_id):
        row = db.scalar(select(Message).where(
            Message.id == message_id, Message.organization_id == int(scope.tenant.value),
            Message.project_id == int(scope.project.id.value)).with_for_update()
            .execution_options(populate_existing=True))
        if row is None:
            raise TrustConflict("resource_unavailable")
        return row

    def extract(self, db, *, scope: RequestScope, claim: DeadlineClaimInput) -> VersionPin:
        claim = DeadlineClaimInput.model_validate(claim.model_dump(mode="json"))
        require_same_tenant(scope.tenant, claim.anchor, claim.message)
        self.guards.allow(db, scope, "claim.extract", claim.message)
        self.guards.enabled(db, scope)
        # Message serializes first insertion and all revisions of its claim anchors.
        self._message(db, scope, int(claim.message.id.value))
        rows = db.scalars(select(DeadlineClaim).where(DeadlineClaim.id == claim.anchor.id.value)
                          .order_by(DeadlineClaim.revision).with_for_update()
                          .execution_options(populate_existing=True)).all()
        if any(r.organization_id != int(scope.tenant.value) or r.message_id != int(claim.message.id.value)
               for r in rows):
            raise TrustConflict("resource_unavailable")
        pins = [p.model_dump(mode="json") for p in claim.evidence]
        keys = [canonical_json(p) for p in pins]
        if keys != sorted(set(keys)):
            raise TrustConflict("invalid_evidence_pins")
        for pin in claim.evidence:
            self.guards.resolve(db, scope, pin, "metadata")
        existing = next((r for r in rows if r.revision == claim.revision), None)
        if existing:
            if (existing.due_date.isoformat() != claim.due_date or existing.timezone != claim.timezone
                    or existing.evidence_pins != pins):
                raise TrustConflict("claim_revision_conflict")
            return revision(claim.anchor, claim.revision)  # Never resets human review.
        if claim.revision != (rows[-1].revision + 1 if rows else 1):
            raise TrustConflict("claim_revision_conflict")
        row = DeadlineClaim(id=claim.anchor.id.value, organization_id=int(scope.tenant.value),
                            revision=claim.revision, message_id=int(claim.message.id.value),
                            due_date=date.fromisoformat(claim.due_date), timezone=claim.timezone,
                            evidence_pins=pins, verification="unverified", record_version=1,
                            provenance={"kind": "task_claim_input", "actor_ref": scope.actor.model_dump(mode="json")})
        db.add(row)
        db.flush()
        pin = revision(claim.anchor, claim.revision)
        v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
            event=AuditAppend(subject=claim.anchor, subject_pin=pin,
                              sequence=sequence(db, claim.anchor), event="CLAIM_EXTRACTED"))
        return pin

    def review(self, db, *, scope: RequestScope, command: ReviewCommand) -> None:
        command = ReviewCommand.model_validate(command.model_dump(mode="json"))
        if command.subject.ref.type != "deadline_claim":
            raise TrustConflict("unsupported_review")
        self.guards.allow(db, scope, "claim.review", command.subject.ref)
        self.guards.enabled(db, scope)
        # Peek only to discover the stream guard, then reread under locks.
        message_id = db.scalar(select(DeadlineClaim.message_id).where(
            DeadlineClaim.id == command.subject.ref.id.value,
            DeadlineClaim.revision == command.subject.value,
            DeadlineClaim.organization_id == int(scope.tenant.value)))
        self._message(db, scope, message_id)
        rows = db.scalars(select(DeadlineClaim).where(DeadlineClaim.id == command.subject.ref.id.value,
            DeadlineClaim.organization_id == int(scope.tenant.value)).order_by(DeadlineClaim.revision)
            .with_for_update().execution_options(populate_existing=True)).all()
        if not rows or rows[-1].revision != command.subject.value:
            raise TrustConflict("claim_revision_conflict")
        row = rows[-1]
        if row.record_version != command.expected_record_version or row.verification != "unverified":
            raise TrustConflict("claim_review_conflict")
        self.guards.resolve(db, scope, command.subject, "review")
        for raw in row.evidence_pins:
            self.guards.resolve(db, scope, VersionPin.model_validate(raw), "review")
        row.verification = command.decision
        row.reviewed_by, row.reviewed_at = int(scope.actor.id.value), self.guards.now()
        row.record_version += 1
        db.flush()
        v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
            event=AuditAppend(subject=command.subject.ref, subject_pin=command.subject,
                              sequence=sequence(db, command.subject.ref),
                              event="CLAIM_CONFIRMED" if command.decision == "confirmed" else "CLAIM_REJECTED"))
