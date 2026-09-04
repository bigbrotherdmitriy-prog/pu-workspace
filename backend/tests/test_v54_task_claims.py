"""Claim writer tests with synthetic resolver; no Task execution wiring."""
import pytest
from sqlalchemy import func, select

from app.action_trust.guards import TrustConflict
from app.core import v54_transactions
from app.core.v54_dto import DeadlineClaimInput
from app.core.v54_interfaces import ReviewCommand
from app.models.task import Task
from app.models.v54_pilot import AuditExtension, DeadlineClaim
from test_v54_action_trust_support import h
from v54_pilot_fixture import pin, ref, uid


def test_extraction_is_unverified_despite_reference_and_confidence(h):
    with h.db.begin():
        p = h.claim(confirm=False)
        row = h.db.get(DeadlineClaim, (p.ref.id.value, 1))
        assert row.verification == "unverified" and row.reviewed_by is None
        assert h.db.scalar(select(func.count()).select_from(Task)) == 0
    with pytest.raises(TrustConflict, match="claim_unverified_or_changed"):
        with h.db.begin():
            h.trust.freeze(h.db, scope=h.requester, envelope=h.envelope())
    # No Task, even though synthetic resolver reports verified evidence.


def test_correction_preserves_anchor_and_old_review_not_new_review(h):
    with h.db.begin():
        first = h.claim()
        second = h.claim(revision_number=2, due_date="2026-09-11", confirm=False)
        assert first.ref == second.ref and second.value == 2
        assert h.db.get(DeadlineClaim, (first.ref.id.value, 1)).verification == "confirmed"
        assert h.db.get(DeadlineClaim, (first.ref.id.value, 2)).verification == "unverified"
        with pytest.raises(TrustConflict, match="claim_revision_conflict"):
            h.claims.review(h.db, scope=h.reviewer,
                command=ReviewCommand(subject=first, expected_record_version=2, decision="rejected"))


def test_duplicate_extraction_preserves_rejection_and_audit(h):
    with h.db.begin():
        p = h.claim(confirm=False)
        h.claims.review(h.db, scope=h.reviewer,
            command=ReviewCommand(subject=p, expected_record_version=1, decision="rejected"))
        before = h.db.scalar(select(func.count()).select_from(AuditExtension))
        assert h.claim(confirm=False) == p
        assert h.db.get(DeadlineClaim, (p.ref.id.value, 1)).verification == "rejected"
        assert h.db.scalar(select(func.count()).select_from(AuditExtension)) == before
        with pytest.raises(TrustConflict, match="claim_review_conflict"):
            h.claims.review(h.db, scope=h.reviewer,
                command=ReviewCommand(subject=p, expected_record_version=2, decision="confirmed"))


@pytest.mark.parametrize("case", ["payload", "gap", "wrong_actor", "stale_review", "denied_evidence", "cross_tenant"])
def test_claim_negative_cases(h, case):
    with h.db.begin():
        p = h.claim(confirm=False)
    with pytest.raises((ValueError, TrustConflict)):
        with h.db.begin():
            if case == "payload":
                h.claim(due_date="2026-09-12", confirm=False)
            elif case == "gap":
                h.claim(revision_number=3, confirm=False)
            elif case == "denied_evidence":
                h.access.bad_pins["evidence"] = {"acl": "deny"}
                h.claim(anchor=102, confirm=False)
            elif case == "cross_tenant":
                value = DeadlineClaimInput(anchor=ref("deadline_claim", uid(103), tenant=2), revision=1,
                    message=ref("message", 6, tenant=2), due_date="2026-09-10", timezone="UTC",
                    evidence=[pin("evidence", uid(16), tenant=2)])
                h.claims.extract(h.db, scope=h.requester, claim=value)
            else:
                h.claims.review(h.db, scope=h.requester if case == "wrong_actor" else h.reviewer,
                    command=ReviewCommand(subject=p, expected_record_version=2 if case == "stale_review" else 1,
                                          decision="confirmed"))
    with h.db.begin():
        assert h.db.get(DeadlineClaim, (uid(101), 1)).verification == "unverified"


def test_claim_audit_failure_rolls_back_in_caller_transaction(h, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic_audit_failure")
    monkeypatch.setattr(v54_transactions, "append_audit", fail)
    with pytest.raises(RuntimeError):
        with h.db.begin():
            h.claim(confirm=False)
    with h.db.begin():
        assert h.db.get(DeadlineClaim, (uid(101), 1)) is None


def test_claim_assertion_immutable_and_no_transaction_autostart(h):
    with pytest.raises(TrustConflict, match="caller_transaction_required"):
        h.claim(confirm=False)
    with h.db.begin():
        h.claim(confirm=False)
    with pytest.raises(ValueError, match="immutable_pilot_assertion"):
        with h.db.begin():
            row = h.db.get(DeadlineClaim, (uid(101), 1))
            row.timezone = "UTC"
            h.db.flush()


def test_actual_high_confidence_evidence_still_needs_human_claim_review(h):
    from app.models.v54_pilot import Evidence, EvidenceAssessment
    from app.core.v54_dto import ActionEnvelope
    from v54_pilot_fixture import NOW
    from datetime import timedelta
    with h.db.begin():
        h.db.add(Evidence(id=uid(116), organization_id=1, source_id=uid(13), source_version_id=uid(15),
            locator={"kind": "whole_object"}, extractor={"name": "synthetic", "version": "1"},
            confidence=1.0, extracted_at=NOW))
        h.db.flush()
        h.db.add(EvidenceAssessment(evidence_id=uid(116), organization_id=1, verification="verified",
            freshness="fresh", availability="available", reviewed_by=3, reviewed_at=NOW,
            checked_at=NOW, valid_until=NOW + timedelta(minutes=5)))
        h.db.flush()
        claim = DeadlineClaimInput(anchor=ref("deadline_claim", uid(101)), revision=1,
            message=ref("message", 6), due_date="2026-09-10", timezone="Europe/Moscow",
            evidence=[pin("evidence", uid(116))])
        h.claims.extract(h.db, scope=h.requester, claim=claim)
    raw = h.envelope().model_dump(mode="json")
    raw["evidence"] = [pin("evidence", uid(116))]
    with pytest.raises(TrustConflict, match="claim_unverified_or_changed"):
        with h.db.begin():
            h.trust.freeze(h.db, scope=h.requester, envelope=ActionEnvelope.model_validate(raw))
    with h.db.begin():
        assert h.db.get(DeadlineClaim, (uid(101), 1)).verification == "unverified"
        assert h.db.scalar(select(func.count()).select_from(Task)) == 0
