"""Minimal executable corpus subset; expectations remain corpus-owned."""
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.integrations.connection_identity import IdentityFacade
from app.models.job import BackgroundJob
from app.models.task import Task
from app.models.v54_pilot import ActionReceipt, ContextRelation, PendingDispatch
from test_v54_pilot_integration import claimed, execute, integrated, prepare, scope


CORPUS = Path(__file__).resolve().parents[2] / "docs/acceptance/v54-corpus/cases"


def expected(case_id):
    for path in CORPUS.glob("*.json"):
        for case in json.loads(path.read_text(encoding="utf8"))["cases"]:
            if case["case_id"] == case_id:
                return case["expected"]["business"]
    raise AssertionError("corpus case missing")


def counts(db):
    return {
        "new_tasks": db.scalar(select(func.count()).select_from(Task)),
        "new_receipts": db.scalar(select(func.count()).select_from(ActionReceipt)),
        "task_projections": db.scalar(select(func.count()).select_from(ContextRelation)
            .where(ContextRelation.receipt_id.is_not(None))),
    }


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
