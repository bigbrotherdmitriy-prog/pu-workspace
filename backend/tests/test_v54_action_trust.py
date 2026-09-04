"""CONFIRM facade/SQLite contracts with explicit doubles, NOT runtime PASS."""
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select

from app.action_trust.guards import TrustConflict
from app.core import v54_transactions
from app.core.v54_dto import ActionEnvelope, canonical_hash
from app.core.v54_refs import ObjectRef
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.task import Task, TaskHistory
from app.models.v54_pilot import (
    ActionApproval, ActionPolicy, ActionReceipt, ActionRevision, AuditExtension,
    DeadlineClaim, EvidenceAssessment, PendingDispatch, PilotAction,
)
from test_v54_action_trust_support import h
from v54_pilot_fixture import NOW, pin, ref, uid


def prepare(h):
    with h.db.begin():
        h.claim()
        e, p, a = h.prepare()
    with h.db.begin():
        b = h.attach_job(e, p, a)
    return e, p, a, b


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_t1_durable_exact_seal_no_enqueue_or_task(h):
    with h.db.begin():
        h.claim()
        e, p, a = h.prepare()
    h.db.expire_all()
    with h.db.begin():
        pending = h.db.get(PendingDispatch, p.ref.id.value)
        assert pending.pending is True and pending.job_id is None
        assert pending.revision == p.value and pending.approval_id == a.id.value
        assert pending.envelope_hash == canonical_hash(e.model_dump(mode="json"))
        before = count(h.db, AuditExtension)
        h.trust.request_dispatch(h.db, scope=h.requester, action=p, approval=a, expected_record_version=1)
        assert count(h.db, AuditExtension) == before
        assert count(h.db, BackgroundJob) == count(h.db, Task) == count(h.db, ActionReceipt) == 0


def test_t2_atomic_double_then_idempotent_receipt_after_job_completion(h):
    e, p, a, b = prepare(h)
    with h.db.begin():
        receipt = h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    with h.db.begin():
        assert count(h.db, Task) == count(h.db, TaskHistory) == count(h.db, ActionReceipt) == 1
        row = h.db.get(ActionReceipt, receipt.id.value)
        assert row.outcome == "APPLIED" and row.approval_id == a.id.value and row.fence == 1
        h.db.get(BackgroundJob, int(b.job.id.value)).status = "completed"
    h.access.time = NOW + timedelta(minutes=20)
    with h.db.begin():
        # Receipt read is authorized history, not a second dispatch with expired grants.
        assert h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation) == receipt
        assert h.mutation.calls == 1
        events = h.db.scalars(select(AuditLog.action).join(AuditExtension, AuditExtension.audit_log_id == AuditLog.id)
            .where(AuditExtension.subject_id == p.ref.id.value).order_by(AuditExtension.sequence)).all()
        assert events == ["v54.ACTION_FROZEN", "v54.APPROVAL_GRANTED", "v54.DISPATCH_REQUESTED",
                          "v54.DISPATCH_AUTHORIZED", "v54.ACTION_SUCCEEDED"]


@pytest.mark.parametrize("failure", ["mutation", "receipt", "audit_authorized", "audit_succeeded", "lease_during_mutation"])
def test_t2_caller_rollback_covers_all_writes(h, monkeypatch, failure):
    e, p, a, b = prepare(h)
    original_append = v54_transactions.append_audit
    def append(db, *, event, **kwargs):
        if event.event == ("DISPATCH_AUTHORIZED" if failure == "audit_authorized" else "ACTION_SUCCEEDED"):
            raise RuntimeError("synthetic_audit_failure")
        return original_append(db, event=event, **kwargs)
    def fail_receipt(*args):
        raise RuntimeError("synthetic_receipt_failure")
    if failure.startswith("audit"):
        monkeypatch.setattr(v54_transactions, "append_audit", append)
    if failure == "mutation":
        h.mutation.fail_after_flush = True
    if failure == "lease_during_mutation":
        h.mutation.after_mutation = lambda: setattr(h.access, "time", NOW + timedelta(minutes=3))
    if failure == "receipt":
        event.listen(ActionReceipt, "before_insert", fail_receipt)
    try:
        with pytest.raises((RuntimeError, TrustConflict)):
            with h.db.begin():
                h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    finally:
        if failure == "receipt":
            event.remove(ActionReceipt, "before_insert", fail_receipt)
    with h.db.begin():
        assert count(h.db, Task) == count(h.db, TaskHistory) == count(h.db, ActionReceipt) == 0
        obj = h.db.get(PilotAction, p.ref.id.value)
        assert obj.business_state == "READY" and obj.reservation_fence == 0
        assert h.db.get(PendingDispatch, obj.id).pending
        assert count(h.db, AuditExtension) == 5  # Claim extract/review + freeze/approve/T1.


@pytest.mark.parametrize("field,value", [
    ("worker_id", "other-worker"), ("job_attempt", 2),
    ("locked_at", NOW + timedelta(seconds=1)), ("command_key", "wrong-key"),
    ("envelope_hash", "0" * 64), ("approval", ObjectRef.model_validate(ref("approval", uid(23)))),
])
def test_stale_dispatch_binding_never_mutates(h, field, value):
    _, _, _, b = prepare(h)
    with pytest.raises(TrustConflict):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b.model_copy(update={field: value}), mutation=h.mutation)
    assert h.mutation.calls == 0


@pytest.mark.parametrize("case", ["expired_lease", "cancel_requested", "wrong_job", "role", "approver_role", "epoch", "policy", "revoked", "expired_approval"])
def test_live_authority_policy_grant_and_queue_ownership(h, case):
    _, p, a, b = prepare(h)
    with h.db.begin():
        job = h.db.get(BackgroundJob, int(b.job.id.value))
        if case == "expired_lease":
            job.lease_expires_at = NOW
        elif case == "cancel_requested":
            job.result = {"cancel_requested": True}
        elif case == "wrong_job":
            h.db.get(PendingDispatch, p.ref.id.value).job_id = None
        elif case == "revoked":
            h.trust.revoke(h.db, scope=h.reviewer, approval=a)
        elif case == "expired_approval":
            job.lease_expires_at = NOW + timedelta(minutes=10)
            h.access.time = NOW + timedelta(minutes=4)
    if case == "role":
        h.access.denied.add("action.execute")
    elif case == "approver_role":
        h.access.denied.add((3, "action.approve"))
    elif case == "epoch":
        h.access.epochs[3] = 2
    elif case == "policy":
        h.access.bad_pins["policy"] = {"version": "changed"}
    with pytest.raises(ValueError):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    assert h.mutation.calls == 0


@pytest.mark.parametrize("case", ["freshness_only", "stale", "revoked", "unverified", "expired_ttl"])
def test_assessment_update_does_not_reseal_but_live_deny_blocks(h, case):
    e, p, a, b = prepare(h)
    with h.db.begin():
        assessment = h.db.get(EvidenceAssessment, uid(16))
        assessment.record_version += 1
        assessment.checked_at = NOW + timedelta(seconds=1)
        if case == "stale":
            assessment.freshness = "stale"
        elif case == "revoked":
            assessment.availability = "unavailable"
        elif case == "unverified":
            assessment.verification = "unverified"
        elif case == "expired_ttl":
            assessment.valid_until = NOW
    if case == "freshness_only":
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
            assert h.db.get(ActionRevision, (p.ref.id.value, 1)).envelope_hash == canonical_hash(e.model_dump(mode="json"))
            assert h.db.get(ActionApproval, a.id.value).state == "GRANTED"
        assert h.mutation.calls == 1
    else:
        with pytest.raises(ValueError):
            with h.db.begin():
                h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
        assert h.mutation.calls == 0


def test_changed_payload_new_revision_invalidates_old_approval_and_pending(h):
    e, p, a, b = prepare(h)
    value = e.model_dump(mode="json")
    value["revision"] = 2
    value["payload"]["title"] = "Synthetic revised wording"
    value["idempotency_key"] = "synthetic-trust-create-2"
    changed = ActionEnvelope.model_validate(value)
    with h.db.begin():
        new = h.trust.freeze(h.db, scope=h.requester, envelope=changed)
        assert new.ref == p.ref and new.value == 2
        assert h.db.get(ActionApproval, a.id.value).state == "INVALIDATED"
        assert h.db.get(PendingDispatch, p.ref.id.value).pending is False
    with pytest.raises(TrustConflict):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    assert h.mutation.calls == 0


@pytest.mark.parametrize("case", ["same_revision_different_payload", "new_id_same_intent", "new_revision_same_command", "approval_command_collision"])
def test_idempotency_conflicts_do_not_create_duplicate(h, case):
    e, p, a, b = prepare(h)
    value = e.model_dump(mode="json")
    if case == "same_revision_different_payload":
        value["payload"]["title"] = "Changed"
    elif case == "new_id_same_intent":
        value["action_ref"] = ref("action", uid(999))
        value["idempotency_key"] = "other"
    elif case == "new_revision_same_command":
        value["revision"] = 2
    with pytest.raises(TrustConflict):
        with h.db.begin():
            if case == "approval_command_collision":
                h.trust.approve(h.db, scope=h.reviewer, action=p, envelope_hash=b.envelope_hash,
                    command_key="approve-create", expires_at=NOW + timedelta(minutes=2))
            else:
                h.trust.freeze(h.db, scope=h.requester, envelope=ActionEnvelope.model_validate(value))
    with h.db.begin():
        assert count(h.db, ActionRevision) == 2  # Foundation + facade revision.
        assert count(h.db, ActionReceipt) == count(h.db, Task) == 0


def test_duplicate_freeze_approve_and_unknown_new_key_protection(h):
    e, p, a, b = prepare(h)
    with h.db.begin():
        before = count(h.db, AuditExtension)
        assert h.trust.freeze(h.db, scope=h.requester, envelope=e) == p
        assert h.approve(e) == a
        assert count(h.db, AuditExtension) == before
        h.db.get(PilotAction, p.ref.id.value).business_state = "UNKNOWN"
    with pytest.raises(TrustConflict, match="outcome_not_replayable"):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    value = e.model_dump(mode="json")
    value["revision"], value["idempotency_key"] = 2, "new-key-cannot-resurrect"
    with pytest.raises(TrustConflict, match="action_not_revisable"):
        with h.db.begin():
            h.trust.freeze(h.db, scope=h.requester, envelope=ActionEnvelope.model_validate(value))
    assert h.mutation.calls == 0


def test_cancel_requires_new_approval_and_preserves_original_receipt(h):
    _, p, a, b = prepare(h)
    with h.db.begin():
        created = h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
        target = h.db.get(ActionReceipt, created.id.value).target_ref
    with h.db.begin():
        cancel = h.envelope(cancel_target=int(target["id"]["value"]))
        cp = h.trust.freeze(h.db, scope=h.requester, envelope=cancel)
    with pytest.raises(TrustConflict, match="approval_not_applicable"):
        with h.db.begin():
            h.trust.request_dispatch(h.db, scope=h.requester, action=cp, approval=a, expected_record_version=2)
    with h.db.begin():
        ca = h.approve(cancel, "approve-cancel")
        h.trust.request_dispatch(h.db, scope=h.requester, action=cp, approval=ca,
            expected_record_version=h.db.get(PilotAction, cp.ref.id.value).record_version)
    with h.db.begin():
        cb = h.attach_job(cancel, cp, ca)
    with h.db.begin():
        cancelled = h.trust.execute(h.db, scope=h.requester, binding=cb, mutation=h.mutation)
    with h.db.begin():
        assert cancelled != created and ca != a
        assert count(h.db, Task) == 1 and count(h.db, ActionReceipt) == 2
        task = h.db.get(Task, int(target["id"]["value"]))
        assert task.status == "cancelled" and task.record_version == 2
        assert h.db.get(PilotAction, p.ref.id.value).business_state == "SUCCEEDED"
        assert h.trust.execute(h.db, scope=h.requester, binding=cb, mutation=h.mutation) == cancelled
        assert h.mutation.calls == 2


@pytest.mark.parametrize("case", ["version", "published", "in_progress"])
def test_cancel_changed_target_refused(h, case):
    _, _, _, b = prepare(h)
    with h.db.begin():
        created = h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
        target_id = int(h.db.get(ActionReceipt, created.id.value).target_ref["id"]["value"])
    with h.db.begin():
        e, p, a = h.prepare(h.envelope(cancel_target=target_id), "approve-cancel")
    with h.db.begin():
        cb = h.attach_job(e, p, a)
        task = h.db.get(Task, target_id)
        if case == "version":
            task.record_version += 1
        elif case == "published":
            task.google_task_id = "synthetic-external-task"
        else:
            task.status = "in_progress"
    with pytest.raises(TrustConflict, match="cancel_target_changed"):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=cb, mutation=h.mutation)
    assert h.mutation.calls == 1


def test_logs_and_audit_do_not_copy_content_or_secrets(h, caplog):
    secret = "synthetic-password-never-log"
    with h.db.begin():
        h.claim()
        raw = h.envelope().model_dump(mode="json")
        raw["payload"]["title"] = secret
        e, p, a = h.prepare(ActionEnvelope.model_validate(raw))
    with h.db.begin():
        b = h.attach_job(e, p, a)
    with h.db.begin():
        h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
        assert all(row.details is None for row in h.db.scalars(select(AuditLog)))
    assert secret not in caplog.text and "recipient@example.test" not in caplog.text


@pytest.mark.parametrize("case", ["AUTO", "external", "scope", "disabled"])
def test_explicit_deny_no_implicit_mode_or_tenant_elevation(h, case):
    with h.db.begin():
        h.claim()
    raw = h.envelope().model_dump(mode="json")
    if case == "AUTO":
        raw["autonomy"] = "AUTO"
    elif case == "external":
        raw["action_type"] = "message.external.send"
    elif case == "scope":
        h.access.denied.add("action.freeze")
    else:
        h.access.enabled = False
    with pytest.raises(ValueError):
        with h.db.begin():
            h.trust.freeze(h.db, scope=h.requester, envelope=ActionEnvelope.model_validate(raw))


def test_new_claim_revision_blocks_old_approved_deadline(h):
    _, _, _, b = prepare(h)
    with h.db.begin():
        h.claim(revision_number=2, due_date="2026-09-11", confirm=False)
    with pytest.raises(TrustConflict, match="claim_unverified_or_changed"):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    assert h.mutation.calls == 0


def test_receipt_read_requires_live_read_permission_and_exact_seal(h):
    _, _, _, b = prepare(h)
    with h.db.begin():
        h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    h.access.denied.add("action.receipt.read")
    with pytest.raises(TrustConflict, match="resource_unavailable"):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    h.access.denied.clear()
    with pytest.raises(TrustConflict, match="dispatch_binding_mismatch"):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b.model_copy(update={"envelope_hash": "1" * 64}), mutation=h.mutation)
    assert h.mutation.calls == 1


def test_policy_revision_change_requires_new_freeze_and_approval(h):
    _, _, _, b = prepare(h)
    with h.db.begin():
        old = h.db.get(ActionPolicy, (uid(22), 1))
        rules = {**old.rules, "revision": 2}
        h.db.add(ActionPolicy(id=old.id, revision=2, organization_id=1, mode="CONFIRM",
            policy_hash=canonical_hash(rules), scope_ref=old.scope_ref, rules=rules,
            valid_until=NOW + timedelta(minutes=5)))
    with pytest.raises(TrustConflict, match="policy_unavailable"):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    assert h.mutation.calls == 0


def test_no_attempt_receipt_on_unknown_or_not_applied(h):
    _, p, _, b = prepare(h)
    for state in ["UNKNOWN", "FAILED_NOT_APPLIED"]:
        with h.db.begin():
            h.db.get(PilotAction, p.ref.id.value).business_state = state
        with pytest.raises(TrustConflict):
            with h.db.begin():
                h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
        with h.db.begin():
            assert count(h.db, ActionReceipt) == 0
    assert h.mutation.calls == 0


def test_t1_audit_failure_rolls_back_pending_dispatch(h, monkeypatch):
    with h.db.begin():
        h.claim()
        e = h.envelope()
        p = h.trust.freeze(h.db, scope=h.requester, envelope=e)
        a = h.approve(e)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic_audit_failure")
    monkeypatch.setattr(v54_transactions, "append_audit", fail)
    with pytest.raises(RuntimeError):
        with h.db.begin():
            h.trust.request_dispatch(h.db, scope=h.requester, action=p, approval=a,
                expected_record_version=h.db.get(PilotAction, p.ref.id.value).record_version)
    with h.db.begin():
        assert h.db.get(PendingDispatch, p.ref.id.value) is None


def test_revoke_is_final_not_freshness_refresh(h):
    e, p, a, b = prepare(h)
    with h.db.begin():
        h.trust.revoke(h.db, scope=h.reviewer, approval=a)
        before = count(h.db, AuditExtension)
        h.trust.revoke(h.db, scope=h.reviewer, approval=a)
        assert count(h.db, AuditExtension) == before
    with h.db.begin():
        assessment = h.db.get(EvidenceAssessment, uid(16))
        assessment.checked_at = NOW + timedelta(seconds=2)
        assessment.record_version += 1
    with pytest.raises(TrustConflict):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    with pytest.raises(TrustConflict, match="approval_command_conflict"):
        with h.db.begin():
            h.approve(e)


def test_worker_reclaim_uses_existing_fields_and_one_business_receipt(h):
    _, _, _, old = prepare(h)
    with h.db.begin():
        job = h.db.get(BackgroundJob, int(old.job.id.value))
        job.worker_id, job.attempts = "replacement-worker", 2
        job.locked_at = NOW + timedelta(seconds=1)
    with pytest.raises(TrustConflict, match="stale_dispatch_binding"):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=old, mutation=h.mutation)
    replacement = old.model_copy(update={"worker_id": "replacement-worker", "job_attempt": 2,
                                        "locked_at": NOW + timedelta(seconds=1)})
    with h.db.begin():
        receipt = h.trust.execute(h.db, scope=h.requester, binding=replacement, mutation=h.mutation)
    with h.db.begin():
        assert h.trust.execute(h.db, scope=h.requester, binding=replacement, mutation=h.mutation) == receipt
        assert count(h.db, ActionReceipt) == count(h.db, Task) == 1
    assert h.mutation.calls == 1  # Sequential simulation, not a two-process PG test.


def test_wrong_mutation_result_rolls_back_without_receipt(h):
    _, _, _, b = prepare(h)
    class WrongResult:
        def apply(self, db, *, scope, binding):
            h.mutation.apply(db, scope=scope, binding=binding)
            return ObjectRef.model_validate(ref("task", 999))
    with pytest.raises(TrustConflict, match="mutation_result_invalid"):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=WrongResult())
    with h.db.begin():
        assert count(h.db, Task) == count(h.db, ActionReceipt) == 0


def test_no_transaction_queue_or_logging_calls_in_facade_sources():
    import ast
    from pathlib import Path
    app = Path(__file__).resolve().parents[1] / "app"
    for path in [app / "task_claims.py", *(app / "action_trust").glob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {"commit", "rollback", "close", "enqueue", "error", "exception", "debug", "info"}
                elif isinstance(node.func, ast.Name):
                    assert node.func.id not in {"print", "enqueue"}
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("app.jobs", "google", "app.task_engine", "app.api.tasks"))


def test_grant_expiring_during_pin_resolution_never_calls_mutation(h, monkeypatch):
    _, _, _, b = prepare(h)
    with h.db.begin():
        h.db.get(BackgroundJob, int(b.job.id.value)).lease_expires_at = NOW + timedelta(minutes=10)
    resolve = h.access.resolve
    def slow_resolve(db, *, scope, pin, operation, lock):
        if operation == "dispatch" and pin.ref.type == "source_version" and pin.ref.id.value == uid(15):
            h.access.time = NOW + timedelta(minutes=4)  # Exact grant expiry, policy still live.
        return resolve(db, scope=scope, pin=pin, operation=operation, lock=lock)
    monkeypatch.setattr(h.access, "resolve", slow_resolve)
    with pytest.raises(TrustConflict):
        with h.db.begin():
            h.trust.execute(h.db, scope=h.requester, binding=b, mutation=h.mutation)
    assert h.mutation.calls == 0
