"""Minimal executable corpus subset; expectations remain corpus-owned."""
from datetime import timedelta
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.action_trust.guards import TrustConflict
from app.core.v54_dto import ActionEnvelope
from app.core.v54_interfaces import ReviewCommand
from app.integrations.connection_identity import IdentityFacade
from app.models.job import BackgroundJob
from app.models.task import Task
from app.models.v54_pilot import (
    ActionReceipt, ContextRelation, DeadlineClaim, Evidence, EvidenceAssessment, PendingDispatch,
)
from test_v54_action_trust_support import h
from test_v54_pilot_integration import claimed, execute, integrated, prepare, scope
from v54_pilot_fixture import NOW, pin, uid


CORPUS = Path(__file__).resolve().parents[2] / "docs/acceptance/v54-corpus/cases"


def expected_case(case_id):
    for path in CORPUS.glob("*.json"):
        for case in json.loads(path.read_text(encoding="utf8"))["cases"]:
            if case["case_id"] == case_id:
                return case["expected"]
    raise AssertionError("corpus case missing")


def expected(case_id):
    return expected_case(case_id)["business"]


def counts(db):
    return {
        "new_tasks": db.scalar(select(func.count()).select_from(Task)),
        "new_receipts": db.scalar(select(func.count()).select_from(ActionReceipt)),
        "task_projections": db.scalar(select(func.count()).select_from(ContextRelation)
            .where(ContextRelation.receipt_id.is_not(None))),
    }


def test_corpus_c07_preserves_exact_time_and_requires_human(h):
    want = expected_case("C07")
    claim_want = want["claims"]
    timestamp = claim_want["timestamp"]
    due_date = claim_want["normalized_date"]
    due_time = timestamp.split("T", 1)[1][:8]
    claim_id, evidence_id = 107, 117

    with h.db.begin():
        h.db.add(Evidence(
            id=uid(evidence_id), organization_id=1, source_id=uid(13), source_version_id=uid(15),
            locator={"kind": "whole_object", "reason_code": "synthetic_c07"},
            extractor={"name": "synthetic", "version": "1"}, confidence=0.2,
            confidence_kind="model", extracted_at=NOW,
        ))
        h.db.flush()
        h.db.add(EvidenceAssessment(
            evidence_id=uid(evidence_id), organization_id=1, verification="verified",
            freshness="fresh", availability="available", checked_at=NOW,
            valid_until=NOW + timedelta(minutes=5), reviewed_by=3, reviewed_at=NOW,
        ))
        claim = h.claim(
            anchor=claim_id, due_date=due_date, due_time=due_time,
            timezone=claim_want["timezone"], evidence_id=evidence_id, confirm=False,
        )
        row = h.db.get(DeadlineClaim, (claim.ref.id.value, 1))
        stored_timestamp = f"{row.due_date.isoformat()}T{row.due_time.isoformat()}{row.timezone[3:]}"
        assert stored_timestamp == timestamp
        assert row.verification == claim_want["review_state"] == "unverified"
        assert h.db.get(Evidence, uid(evidence_id)).confidence == 0.2
        assert h.db.scalar(select(func.count()).select_from(Task)) == want["business"]["new_tasks"] == 0

    with pytest.raises(TrustConflict, match="claim_unverified_or_changed"):
        with h.db.begin():
            raw = h.envelope().model_dump(mode="json")
            raw["claim"] = pin("deadline_claim", uid(claim_id))
            raw["evidence"] = [pin("evidence", uid(evidence_id))]
            raw["payload"]["due_date"] = due_date
            h.trust.freeze(h.db, scope=h.requester, envelope=ActionEnvelope.model_validate(raw))

    with h.db.begin():
        h.claims.review(h.db, scope=h.reviewer, command=ReviewCommand(
            subject=claim, expected_record_version=1, decision="confirmed",
        ))

    with pytest.raises(TrustConflict, match="claim_precision_unsupported"):
        with h.db.begin():
            raw = h.envelope().model_dump(mode="json")
            raw["claim"] = pin("deadline_claim", uid(claim_id))
            raw["evidence"] = [pin("evidence", uid(evidence_id))]
            raw["payload"]["due_date"] = due_date
            h.trust.freeze(h.db, scope=h.requester, envelope=ActionEnvelope.model_validate(raw))

    with h.db.begin():
        assert h.db.scalar(select(func.count()).select_from(Task)) == 0
        assert h.db.scalar(select(func.count()).select_from(ActionReceipt)) == 0


def test_corpus_s06_receipt_replay(integrated):
    want = expected("S06")
    envelope = prepare(integrated)
    first, payload, owner = execute(integrated, envelope)
    assert integrated[2].execute(payload, owner) == first
    with integrated[0]() as db:
        got = counts(db)
    for key in ("new_tasks", "new_receipts", "task_projections"):
        assert got[key] == want[key]


def test_corpus_p02_revocation_blocks_effect(integrated):
    want = expected("P02")
    envelope, payload, owner = claimed(integrated)
    with integrated[0].begin() as db:
        IdentityFacade(integrated[1].policy, integrated[1].clock).revoke(
            db, scope=scope(), identity=integrated[3], expected_version=1)
    with pytest.raises(ValueError):
        integrated[2].execute(payload, owner)
    with integrated[0]() as db:
        got = counts(db)
    assert got["new_tasks"] == want["new_tasks"]
    assert got["new_receipts"] == want["new_receipts"]


def test_corpus_p06_disabled_never_enqueues(integrated):
    want = expected("P06")
    envelope = prepare(integrated)
    integrated[1].enabled = False
    with pytest.raises(ValueError):
        integrated[2].enqueue_action(envelope.action_ref.id.value,
                                     "00000000-0000-4000-8000-000000000999")
    with integrated[0]() as db:
        got = counts(db)
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0
        assert db.get(PendingDispatch, envelope.action_ref.id.value).pending
    assert got["new_tasks"] == want["new_tasks"]
    assert got["new_receipts"] == want["new_receipts"]


def test_corpus_s09_separate_cancel_approval(integrated):
    from test_v54_pilot_integration import test_real_abc_task_receipt_projection_and_separate_cancel
    test_real_abc_task_receipt_projection_and_separate_cancel(integrated)
    want = expected("S09")
    with integrated[0]() as db:
        task = db.scalar(select(Task))
        got = counts(db)
    assert got["new_tasks"] == want["new_tasks"]
    assert got["new_receipts"] == want["new_receipts"]
    assert task.status == want["task_status"]
