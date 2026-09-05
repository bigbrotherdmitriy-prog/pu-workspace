from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.database import Base
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.v54_provider_action import ProviderAction, ProviderOutcomeObservation
from app.provider_actions.contracts import ActionEnvelope, ProviderActionError
from app.provider_actions.runtime import KIND, ProviderActionRuntime, install_synthetic_runtime
from app.provider_actions.synthetic import Fault, ProcessExitAfterEffect, StrictSyntheticProvider, SyntheticAuthority


NOW = datetime(2035, 1, 1, tzinfo=timezone.utc)


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def envelope(**changes) -> ActionEnvelope:
    values = dict(
        action_id="action-1",
        revision=1,
        organization_id=7,
        project_id=11,
        mailbox_key=digest("synthetic-mailbox"),
        provider="synthetic",
        mode="CONFIRM",
        synthetic_only=True,
        action_kind="synthetic.effect.apply",
        reversibility="REVERSIBLE",
        payload_hash=digest("payload with recipient@example.test and body-marker"),
        command_key="command-1",
        idempotency_key="provider-action-1",
        context_revision=3,
        evidence_pins=("evidence-1@3", "attachment-2@1"),
        authority_epoch=5,
        capability_version=2,
        credential_generation=4,
    )
    values.update(changes)
    return ActionEnvelope(**values)


@pytest.fixture
def runtime():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    provider = StrictSyntheticProvider()
    authority = SyntheticAuthority(now=lambda: NOW)
    value = envelope()
    provider.register(value.mailbox_key, capability_version=2, credential_generation=4)
    authority.grant(value, valid_until=NOW + timedelta(hours=1))
    service = ProviderActionRuntime(sessions=sessions, adapter=provider, authority=authority, clock=lambda: NOW)
    yield service, provider, authority, sessions
    engine.dispose()


def prepare(runtime, value: ActionEnvelope | None = None):
    service, _, authority, _ = runtime
    value = value or envelope()
    authority.grant(value, valid_until=NOW + timedelta(hours=1))
    service.freeze(value, actor_id="user-9", correlation_id="correlation-freeze")
    approval = service.approve(value.action_id, value.revision, approval_id=f"approval-{value.action_id}",
                               actor_id="user-9", expires_at=NOW + timedelta(minutes=30),
                               correlation_id="correlation-approve")
    service.request_dispatch(value.action_id, value.revision, approval.id,
                             actor_id="user-9", correlation_id="correlation-request")
    job_id = service.enqueue_action(value.action_id, value.revision)
    return value, approval, job_id


def claim(sessions, job_id: int, worker: str, attempt: int = 1):
    with sessions.begin() as db:
        job = db.get(BackgroundJob, job_id)
        job.status = "running"
        job.worker_id = worker
        job.attempts = attempt
        job.locked_at = NOW
        job.lease_expires_at = NOW + timedelta(minutes=5)
    return job_id, worker, attempt, NOW


def payload(value: ActionEnvelope) -> dict:
    return {"organization_id": value.organization_id, "action_id": value.action_id, "revision": value.revision}


def error(code: str, call):
    with pytest.raises(ProviderActionError) as caught:
        call()
    assert caught.value.code == code
    assert str(caught.value) == code


def test_confirm_only_exact_binding_and_existing_background_job(runtime):
    service, provider, _, sessions = runtime
    value, approval, job_id = prepare(runtime)
    result = service.execute_job(payload(value), claim(sessions, job_id, "worker-a"))

    assert result["outcome"] == "APPLIED"
    assert provider.counters == {"dispatch": 1, "lookup": 0, "effects": 1}
    with sessions() as db:
        job = db.get(BackgroundJob, job_id)
        row = db.get(ProviderAction, (value.action_id, value.revision))
        assert job.kind == KIND
        assert job.payload == payload(value)  # ID-only transport payload
        assert job.idempotency_key == value.idempotency_key
        assert row.payload_hash == approval.payload_hash == value.payload_hash
        assert row.envelope_hash == approval.envelope_hash


def test_existing_worker_handler_and_outbox_recovery(runtime):
    service, provider, _, sessions = runtime
    value = envelope()
    authority = runtime[2]
    authority.grant(value, valid_until=NOW + timedelta(hours=1))
    service.freeze(value, actor_id="user-9", correlation_id="freeze")
    approval = service.approve(value.action_id, 1, approval_id="approval-handler", actor_id="user-9",
                               expires_at=NOW + timedelta(minutes=30), correlation_id="approve")
    service.request_dispatch(value.action_id, 1, approval.id, actor_id="user-9", correlation_id="request")
    assert service.recover_outbox() == 1
    with sessions() as db:
        job_id = db.scalar(select(BackgroundJob.id).where(BackgroundJob.kind == KIND))
    owner = claim(sessions, job_id, "worker-handler")

    from app.jobs.handlers import run
    from app.jobs.queue import execution_owner
    install_synthetic_runtime(service)
    try:
        with execution_owner(job_id, "worker-handler", attempt=1, locked_at=NOW):
            result = run(KIND, payload(value))
    finally:
        install_synthetic_runtime(None)

    assert result["outcome"] == "APPLIED"
    assert provider.counters["effects"] == 1


@pytest.mark.parametrize("field", ["project_id", "mailbox_key", "command_key", "idempotency_key", "payload_hash"])
def test_exact_envelope_and_approval_binding_rejects_tampering(runtime, field):
    service, provider, _, sessions = runtime
    value, _, job_id = prepare(runtime)
    with sessions.begin() as db:
        forged = digest("forged") if field in {"mailbox_key", "payload_hash"} else 99 if field == "project_id" else "forged"
        # Simulate corruption/bulk SQL outside the authorized ORM write path.
        db.execute(update(ProviderAction).where(ProviderAction.action_id == value.action_id).values({field: forged}))
    error("dispatch_binding_mismatch", lambda: service.execute_job(payload(value), claim(sessions, job_id, "worker-a")))
    assert provider.counters["dispatch"] == 0


@pytest.mark.parametrize("change,code", [
    ("authority", "authority_stale"), ("capability", "capability_stale"),
    ("credential", "credential_stale"), ("evidence", "evidence_stale"),
])
def test_live_guards_fail_closed_before_effect(runtime, change, code):
    service, provider, authority, sessions = runtime
    value, _, job_id = prepare(runtime)
    getattr(authority, f"revoke_{change}")(value)
    error(code, lambda: service.execute_job(payload(value), claim(sessions, job_id, "worker-a")))
    assert provider.counters["dispatch"] == 0


def test_timeout_before_is_append_only_not_applied_and_retry_safe(runtime):
    service, provider, _, sessions = runtime
    value, _, job_id = prepare(runtime)
    provider.inject_fault(value.mailbox_key, value.command_key, Fault.TIMEOUT_BEFORE_EFFECT)
    result = service.execute_job(payload(value), claim(sessions, job_id, "worker-a"))

    assert result == {"action_id": value.action_id, "revision": 1, "outcome": "NOT_APPLIED", "retry_safe": True}
    assert provider.counters["effects"] == 0
    with sessions() as db:
        observation = db.scalar(select(ProviderOutcomeObservation))
        assert observation.outcome == "NOT_APPLIED" and observation.retry_safe is True
        observation.outcome = "APPLIED"
        with pytest.raises(ValueError, match="append_only_provider_observation"):
            db.flush()


def test_timeout_after_is_unknown_without_blind_retry_then_scoped_reconcile(runtime):
    service, provider, _, sessions = runtime
    value, _, job_id = prepare(runtime, envelope(action_kind="synthetic.effect.send", reversibility="IRREVERSIBLE"))
    provider.inject_fault(value.mailbox_key, value.command_key, Fault.TIMEOUT_AFTER_EFFECT)

    first = service.execute_job(payload(value), claim(sessions, job_id, "worker-a"))
    repeated = service.execute_job(payload(value), claim(sessions, job_id, "worker-b", 2))

    assert first["outcome"] == "UNKNOWN"
    assert repeated["outcome"] == "APPLIED"
    assert provider.counters == {"dispatch": 1, "lookup": 1, "effects": 1}


def test_malformed_provider_receipt_is_unknown_not_queue_retry(runtime):
    service, provider, _, sessions = runtime
    value, _, job_id = prepare(runtime)
    original = provider.dispatch

    def malformed(request):
        receipt = original(request)
        return replace(receipt, payload_hash=digest("wrong-payload"))

    provider.dispatch = malformed
    result = service.execute_job(payload(value), claim(sessions, job_id, "worker-a"))
    assert result == {"action_id": value.action_id, "revision": 1, "outcome": "UNKNOWN", "retry_safe": False}
    assert provider.counters["effects"] == 1


def test_process_death_after_effect_second_worker_only_reconciles(runtime):
    service, provider, _, sessions = runtime
    value, _, job_id = prepare(runtime)
    provider.inject_fault(value.mailbox_key, value.command_key, Fault.PROCESS_EXIT_AFTER_EFFECT)

    with pytest.raises(ProcessExitAfterEffect):
        service.execute_job(payload(value), claim(sessions, job_id, "worker-a"))
    recovered = service.execute_job(payload(value), claim(sessions, job_id, "worker-b", 2))

    assert recovered["outcome"] == "APPLIED"
    assert provider.counters == {"dispatch": 1, "lookup": 1, "effects": 1}
    with sessions() as db:
        events = list(db.scalars(select(ProviderOutcomeObservation).order_by(ProviderOutcomeObservation.sequence)))
        assert [(row.outcome, row.source) for row in events] == [("APPLIED", "PROCESS_RECOVERY")]


def test_reconciliation_rechecks_scope_and_accepts_late_receipt(runtime):
    service, provider, authority, sessions = runtime
    value, _, job_id = prepare(runtime)
    provider.inject_fault(value.mailbox_key, value.command_key, Fault.TIMEOUT_AFTER_EFFECT)
    assert service.execute_job(payload(value), claim(sessions, job_id, "worker-a"))["outcome"] == "UNKNOWN"
    authority.revoke_evidence(value)
    error("evidence_stale", lambda: service.reconcile(value.action_id, value.revision,
                                                       actor_id="user-9", correlation_id="reconcile-1"))
    assert provider.counters["lookup"] == 0
    authority.grant(value, valid_until=NOW + timedelta(hours=1))
    result = service.reconcile(value.action_id, value.revision, actor_id="user-9", correlation_id="reconcile-2")
    assert result["outcome"] == "APPLIED"
    with sessions() as db:
        rows = list(db.scalars(select(ProviderOutcomeObservation).order_by(ProviderOutcomeObservation.sequence)))
        assert [row.outcome for row in rows] == ["UNKNOWN", "APPLIED"]
        assert rows[-1].late is True


@pytest.mark.parametrize("relation_kind,original_reversibility,new_kind", [
    ("ROLLBACK", "REVERSIBLE", "synthetic.effect.rollback"),
    ("COMPENSATION", "COMPENSATABLE", "synthetic.effect.compensate"),
    ("CORRECTIVE", "IRREVERSIBLE", "synthetic.effect.corrective"),
])
def test_rollback_compensation_and_corrective_are_new_approved_actions(runtime, relation_kind,
                                                                        original_reversibility, new_kind):
    service, provider, authority, sessions = runtime
    original = envelope(action_kind="synthetic.effect.send" if relation_kind == "CORRECTIVE" else "synthetic.effect.apply",
                        reversibility=original_reversibility)
    original, _, job_id = prepare(runtime, original)
    assert service.execute_job(payload(original), claim(sessions, job_id, "worker-a"))["outcome"] == "APPLIED"

    followup = envelope(action_id=f"action-{relation_kind.lower()}", command_key=f"command-{relation_kind.lower()}",
                        idempotency_key=f"provider-{relation_kind.lower()}", payload_hash=digest(relation_kind),
                        action_kind=new_kind, reversibility="IRREVERSIBLE" if relation_kind == "CORRECTIVE" else "REVERSIBLE",
                        relation_kind=relation_kind, relation_action_id=original.action_id)
    authority.grant(followup, valid_until=NOW + timedelta(hours=1))
    service.freeze(followup, actor_id="user-9", correlation_id="followup-freeze")
    error("approval_required", lambda: service.request_dispatch(followup.action_id, 1, "missing",
                                                                 actor_id="user-9", correlation_id="bad"))
    approval = service.approve(followup.action_id, 1, approval_id=f"approval-{relation_kind}",
                               actor_id="user-9", expires_at=NOW + timedelta(minutes=10), correlation_id="followup-approve")
    service.request_dispatch(followup.action_id, 1, approval.id, actor_id="user-9", correlation_id="followup-request")
    followup_job = service.enqueue_action(followup.action_id, 1)
    assert service.execute_job(payload(followup), claim(sessions, followup_job, "worker-b"))["outcome"] == "APPLIED"
    assert provider.counters["effects"] == 2
    with sessions() as db:
        actions = list(db.scalars(select(ProviderAction)))
        approvals = {row.action_id for row in db.scalars(select(app.models.v54_provider_action.ProviderActionApproval))}
        audits = list(db.scalars(select(AuditLog).where(AuditLog.action.like("v54.provider.%"))))
        assert len(actions) == 2 and {row.action_id for row in actions} <= approvals
        assert any(row.action == "v54.provider.approval_granted" for row in audits)


def test_auto_live_provider_and_sensitive_logging_are_impossible(runtime):
    service, _, authority, sessions = runtime
    error("confirm_only", lambda: envelope(mode="AUTO"))
    error("synthetic_only", lambda: envelope(provider="gmail"))

    value = envelope(project_id=99)
    authority.grant(value, valid_until=NOW + timedelta(hours=1))
    service.freeze(value, actor_id="user-9", correlation_id="safe-audit")
    with sessions() as db:
        observable = repr(list(db.scalars(select(AuditLog)))) + repr(list(db.scalars(select(ProviderAction))))
    assert "recipient@example.test" not in observable
    assert "body-marker" not in observable
    assert "postgresql://" not in observable
