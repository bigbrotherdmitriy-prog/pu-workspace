"""Synthetic ledger/late-receipt guards; PostgreSQL races live in their own suite."""
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from test_v54_provider_action_runtime import runtime, prepare, claim, payload, NOW
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.v54_provider_action import ProviderAction, ProviderOutcomeObservation
from app.provider_actions.contracts import ProviderActionError
from app.provider_actions.recovery_guards import ABSENCE_CODE, record_republication, require_republication_dispatch
from app.provider_actions.synthetic import Fault


def unknown(runtime):
    service, provider, _, sessions = runtime
    value, _, job_id = prepare(runtime)
    provider.inject_fault(value.mailbox_key, value.command_key, Fault.TIMEOUT_AFTER_EFFECT)
    assert service.execute_job(payload(value), claim(sessions, job_id, "original"))["outcome"] == "UNKNOWN"
    receipt = provider._effects[(value.mailbox_key, value.command_key)]
    return value, job_id, receipt


def resolve(service, value):
    return service._record(value.action_id, value.revision, "NOT_APPLIED", source="RECONCILE",
        job_id=None, retry_safe=True, safe_code=ABSENCE_CODE)


def next_revision(runtime, previous, *, linked=True):
    service, _, authority, sessions = runtime
    new = replace(previous, revision=previous.revision + 1,
        command_key=f"command-{previous.revision+1}", idempotency_key=f"idempotency-{previous.revision+1}")
    authority.grant(new, valid_until=NOW + timedelta(hours=1))
    service.freeze(new, actor_id="user", correlation_id="synthetic")
    approval = service.approve(new.action_id, new.revision, approval_id=f"approval-{new.revision}",
        actor_id="user", expires_at=NOW+timedelta(minutes=30), correlation_id="synthetic")
    if linked:
        with sessions.begin() as db:
            record_republication(db, db.get(ProviderAction,(previous.action_id,previous.revision)),
                db.get(ProviderAction,(new.action_id,new.revision)))
    service.request_dispatch(new.action_id,new.revision,approval.id,actor_id="user",correlation_id="synthetic")
    return new, service.enqueue_action(new.action_id,new.revision)


def test_failure_and_absence_safe_codes_do_not_deduplicate_each_other(runtime, monkeypatch):
    service, provider, _, sessions = runtime
    value, _, _ = unknown(runtime)
    def fail(_request):raise RuntimeError("synthetic unavailable")
    for lookup, expected in ((fail,"provider_lookup_failed"),(lambda _:None,"receipt_not_found"),(fail,"provider_lookup_failed")):
        monkeypatch.setattr(provider,"lookup",lookup)
        service.reconcile(value.action_id,1,actor_id="user",correlation_id="synthetic")
        with sessions() as db:
            assert service._latest(db,value.action_id,1).safe_code==expected


def test_stale_negative_cannot_erase_new_positive_but_fresh_deletion_can(runtime,monkeypatch):
    service,provider,_,sessions=runtime
    value,_,receipt=unknown(runtime)
    def late_then_empty(_request):
        service.record_late_receipt(value.action_id,1,receipt,actor_id="user",correlation_id="late")
        return None
    monkeypatch.setattr(provider,"lookup",late_then_empty)
    assert service.reconcile(value.action_id,1,actor_id="user",correlation_id="negative")["outcome"]=="APPLIED"
    with sessions() as db:
        assert [x.outcome for x in db.scalars(select(ProviderOutcomeObservation).order_by(ProviderOutcomeObservation.sequence))]==["UNKNOWN","APPLIED"]
    monkeypatch.setattr(provider,"lookup",lambda _:None)
    assert service.reconcile(value.action_id,1,actor_id="user",correlation_id="fresh-deletion")["outcome"]=="UNKNOWN"


def test_late_positive_after_human_absence_fences_only_linked_republication(runtime):
    service,provider,_,sessions=runtime
    value,_,receipt=unknown(runtime);resolve(service,value)
    retry,retry_job=next_revision(runtime,value)
    normal,normal_job=next_revision(runtime,retry,linked=False)
    result=service.record_late_receipt(value.action_id,1,receipt,actor_id="user",correlation_id="late")
    assert result["outcome"]=="APPLIED"
    with sessions() as db:
        assert db.get(ProviderAction,(value.action_id,1)).state=="APPLIED"
        assert db.get(ProviderAction,(value.action_id,2)).state=="BLOCKED"
        assert db.get(ProviderAction,(value.action_id,3)).state=="READY"
        require_republication_dispatch(db,db.get(ProviderAction,(normal.action_id,normal.revision)))
    with pytest.raises(ProviderActionError,match="unknown_requires_reconciliation"):
        service.execute_job(payload(retry),claim(sessions,retry_job,"retry-worker"))
    assert provider.counters["effects"]==1


def test_late_positive_retains_already_dispatched_recovery_and_audits_conflict(runtime):
    service,provider,_,sessions=runtime
    value,_,receipt=unknown(runtime);resolve(service,value)
    retry,job=next_revision(runtime,value)
    assert service.execute_job(payload(retry),claim(sessions,job,"retry-worker"))["outcome"]=="APPLIED"
    assert service.record_late_receipt(value.action_id,1,receipt,actor_id="user",correlation_id="late")["outcome"]=="APPLIED"
    with sessions() as db:
        assert db.get(ProviderAction,(value.action_id,2)).state=="APPLIED"
        log=db.scalar(select(AuditLog).where(AuditLog.action=="v54.provider.recovery_late_positive"))
        assert '"already_dispatched": true' in log.details
    assert provider.counters["effects"]==2  # Evidence is preserved; no retroactive exactly-once claim.


def test_late_positive_fences_transitive_recovery_descendants(runtime):
    service,provider,_,sessions=runtime
    value,_,receipt=unknown(runtime);resolve(service,value)
    second,second_job=next_revision(runtime,value)
    provider.inject_fault(second.mailbox_key,second.command_key,Fault.TIMEOUT_AFTER_EFFECT)
    assert service.execute_job(payload(second),claim(sessions,second_job,"second"))["outcome"]=="UNKNOWN"
    resolve(service,second)
    third,third_job=next_revision(runtime,second)
    service.record_late_receipt(value.action_id,1,receipt,actor_id="user",correlation_id="late")
    with sessions() as db:
        assert db.get(ProviderAction,(value.action_id,2)).state=="NOT_APPLIED"
        assert db.get(ProviderAction,(value.action_id,3)).state=="BLOCKED"
    with pytest.raises(ProviderActionError,match="unknown_requires_reconciliation"):
        service.execute_job(payload(third),claim(sessions,third_job,"third"))
    assert provider.counters["effects"]==2


def test_late_positive_does_not_override_nonhuman_not_applied(runtime):
    service,_,_,_=runtime
    value,_,receipt=unknown(runtime)
    service._record(value.action_id,1,"NOT_APPLIED",source="DISPATCH",job_id=None,retry_safe=True,safe_code="timeout_before_effect")
    with pytest.raises(ProviderActionError,match="outcome_not_reconcilable"):
        service.record_late_receipt(value.action_id,1,receipt,actor_id="user",correlation_id="late")


def test_reclaimed_job_cannot_commit_lookup_result(runtime,monkeypatch):
    service,provider,_,sessions=runtime
    value,job_id,receipt=unknown(runtime)
    def stale_lookup(_request):
        with sessions.begin() as db:
            job=db.get(BackgroundJob,job_id);job.worker_id="new-worker";job.attempts+=1
        return receipt
    monkeypatch.setattr(provider,"lookup",stale_lookup)
    with pytest.raises(ProviderActionError,match="dispatch_binding_mismatch"):
        service.execute_job(payload(value),claim(sessions,job_id,"old-worker",2))
    with sessions() as db:
        assert service._latest(db,value.action_id,1).outcome=="UNKNOWN"


def test_revoked_authority_during_lookup_cannot_commit_result(runtime,monkeypatch):
    service,provider,authority,sessions=runtime
    value,_,receipt=unknown(runtime)
    def revoked_lookup(_request):
        authority.revoke_authority(value)
        return receipt
    monkeypatch.setattr(provider,"lookup",revoked_lookup)
    with pytest.raises(ProviderActionError,match="authority_stale"):
        service.reconcile(value.action_id,1,actor_id="user",correlation_id="lookup")
    with sessions() as db:
        assert service._latest(db,value.action_id,1).outcome=="UNKNOWN"
