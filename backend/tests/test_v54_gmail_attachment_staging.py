import base64
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import gmail
from app.jobs.handlers import notify_outcome, run
from app.jobs.queue import claim, execution_owner, recover_expired, request_cancel, utcnow
from app.mailbox_identity.service import MailboxIdentityService
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.mailbox_identity import MailboxCutoverFlags
from app.staging.gmail import (
    GMAIL_ATTACHMENT_JOB_KIND,
    GmailAttachmentBinding,
    GmailAttachmentDenied,
    GmailAttachmentIntegrityError,
    GmailAttachmentProcessResult,
    GmailAttachmentStageResult,
    install_gmail_attachment_lifecycle,
    recover_gmail_attachment_jobs,
)
from test_v54_mailbox_identity import command, world


STAGING_ID = "5a" * 16
SECRET_BODY = b"synthetic attachment content"
RAW_MESSAGE_ID = "provider-message-synthetic"
RAW_ATTACHMENT_ID = "provider-attachment-secret"


class FakeLifecycle:
    def __init__(self):
        self.binding = None
        self.body_reads = 0
        self.process_reads = 0
        self.outcomes = []
        self.pending = []
        self.fail_integrity = False

    def admit_and_stage(self, _db, binding, provider):
        duplicate = self.binding is not None
        if not duplicate:
            opened = provider.open()
            assert opened.observed_size == len(SECRET_BODY)
            assert opened.stream.read() == SECRET_BODY
            self.body_reads += 1
            self.binding = binding
        else:
            assert binding == self.binding
        return GmailAttachmentStageResult(STAGING_ID, duplicate=duplicate)

    def describe(self, _db, staging_id):
        assert staging_id == STAGING_ID
        return self.binding

    def process(self, _db, staging_id, claim, authorize_read):
        assert staging_id == STAGING_ID and claim.attempt > 0
        with authorize_read():
            self.process_reads += 1
            if self.fail_integrity:
                raise GmailAttachmentIntegrityError("ciphertext_integrity_failed")
        with authorize_read():
            pass
        return GmailAttachmentProcessResult("completed", document_id=71, tasks=1)

    def on_job_outcome(self, _db, staging_id, status):
        assert staging_id == STAGING_ID
        self.outcomes.append(status)

    def recover_pending(self, _db, limit):
        assert limit > 0
        return tuple(self.pending)


class FakeGmail:
    def __init__(self, calls, *, body=SECRET_BODY, reported_size=None):
        self.calls, self.body, self.reported_size = calls, body, reported_size

    def users(self): return self
    def messages(self): return self
    def attachments(self): return self

    def get(self, **kwargs):
        self.calls.append(kwargs)
        payload = {"data": base64.urlsafe_b64encode(self.body).decode()}
        if self.reported_size is not None:
            payload["size"] = self.reported_size
        return SimpleNamespace(execute=lambda: payload)


@pytest.fixture
def staged_world(db_session, user_factory, monkeypatch):
    w = world(db_session, user_factory)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    flags = w.db.scalar(select(MailboxCutoverFlags))
    flags.primary_read = flags.actions = True
    w.message.attachments_json = (
        '[{"name":"private-name.txt","mime_type":"text/plain","size":%d,'
        '"attachment_id":"%s","document_external_id":"private-external"}]'
        % (len(SECRET_BODY), RAW_ATTACHMENT_ID)
    )
    w.db.commit()
    lifecycle = FakeLifecycle()
    install_gmail_attachment_lifecycle(lifecycle)
    calls = []
    fake = FakeGmail(calls)
    monkeypatch.setattr(
        gmail, "google_workspace_for_mailbox",
        lambda *_a, **_k: SimpleNamespace(service=lambda *_a, **_k: fake),
    )
    monkeypatch.setattr("app.database.SessionLocal", lambda: nullcontext(w.db))
    yield w, lifecycle, calls
    install_gmail_attachment_lifecycle(None)


def _enqueue(staged_world):
    w, lifecycle, calls = staged_world
    result = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    return w, lifecycle, calls, result


def test_confirm_ingress_downloads_once_and_enqueues_only_opaque_staging_id(staged_world):
    w, lifecycle, calls, first = _enqueue(staged_world)
    second = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    jobs = list(w.db.scalars(select(BackgroundJob)))
    assert first["job_id"] == second["job_id"] == jobs[0].id
    assert second["already_queued"] is True
    assert lifecycle.body_reads == 1
    assert calls == [{"userId": "me", "messageId": RAW_MESSAGE_ID, "id": RAW_ATTACHMENT_ID}]
    assert jobs[0].kind == GMAIL_ATTACHMENT_JOB_KIND
    assert jobs[0].payload == {"staging_id": STAGING_ID}
    serialized = str(jobs[0].payload) + (jobs[0].idempotency_key or "")
    for forbidden in (RAW_MESSAGE_ID, RAW_ATTACHMENT_ID, "private-name", "private-external",
                      SECRET_BODY.decode(), "base64", "token", "\\", "/"):
        assert forbidden not in serialized
    audits = list(w.db.scalars(select(AuditLog).where(AuditLog.action == "gmail_attachment_staged")))
    assert audits
    audit_text = " ".join(row.details or "" for row in audits)
    assert STAGING_ID in audit_text
    assert not any(value in audit_text for value in (RAW_MESSAGE_ID, RAW_ATTACHMENT_ID, "private-name", SECRET_BODY.decode()))


@pytest.mark.parametrize(
    "mutation", ["rollout", "generation", "revoke", "epoch", "project", "origin", "mime", "size"],
)
def test_live_scope_rotation_and_metadata_changes_deny_every_worker_read(staged_world, mutation):
    w, lifecycle, _calls, result = _enqueue(staged_world)
    job = claim(w.db, "worker-old", lease_seconds=60)
    if mutation == "rollout":
        w.db.scalar(select(MailboxCutoverFlags)).actions = False
    elif mutation == "generation":
        w.identity.credential_generation += 1
    elif mutation == "revoke":
        w.identity.state = "revoked"
    elif mutation == "epoch":
        w.identity.binding_epoch += 1
    elif mutation == "project":
        lifecycle.binding = replace(lifecycle.binding, project_id=w.project.id + 100)
    elif mutation == "origin":
        w.source.sync_state = "discovered"
    elif mutation == "mime":
        w.message.attachments_json = w.message.attachments_json.replace("text/plain", "application/pdf")
    else:
        w.message.attachments_json = w.message.attachments_json.replace(
            f'"size":{len(SECRET_BODY)}', f'"size":{len(SECRET_BODY) + 1}',
        )
    w.db.commit()
    with execution_owner(job.id, "worker-old", attempt=job.attempts, locked_at=job.locked_at):
        with pytest.raises(GmailAttachmentDenied):
            run(job.kind, dict(job.payload))
    assert lifecycle.process_reads == 0
    assert result["staging_id"] == STAGING_ID


def test_expired_lease_recovery_reuses_staging_and_new_claim_fence(staged_world):
    w, lifecycle, _calls, _ = _enqueue(staged_world)
    old = claim(w.db, "worker-old", lease_seconds=60)
    old_locked_at = old.locked_at
    old.lease_expires_at = utcnow() - timedelta(seconds=1)
    w.db.commit()
    assert recover_expired(w.db) == 1
    with execution_owner(old.id, "worker-old", attempt=1, locked_at=old_locked_at):
        with pytest.raises(GmailAttachmentDenied):
            run(old.kind, dict(old.payload))
    assert lifecycle.process_reads == 0
    recovered = claim(w.db, "worker-new", lease_seconds=60)
    assert recovered.id == old.id and recovered.attempts == 2
    with execution_owner(recovered.id, "worker-new", attempt=recovered.attempts, locked_at=recovered.locked_at):
        result = run(recovered.kind, dict(recovered.payload))
    assert result["document_id"] == 71 and lifecycle.process_reads == 1
    assert lifecycle.body_reads == 1


def test_running_cancel_performs_no_new_read_and_returns_cleanup_outcome(staged_world):
    w, lifecycle, _calls, _ = _enqueue(staged_world)
    job = claim(w.db, "worker", lease_seconds=60)
    assert request_cancel(w.db, job.id, allow_running=True) == "cancellation_requested"
    with execution_owner(job.id, "worker", attempt=job.attempts, locked_at=job.locked_at):
        result = run(job.kind, dict(job.payload))
    assert result["cancelled"] is True
    assert lifecycle.process_reads == 0


def test_terminal_and_failed_retention_hooks_are_idempotently_routed(staged_world):
    _w, lifecycle, _calls, _ = _enqueue(staged_world)
    payload = {"staging_id": STAGING_ID}
    for status in ("retrying", "dead_letter", "completed", "cancelled", "completed"):
        notify_outcome(GMAIL_ATTACHMENT_JOB_KIND, payload, status)
    assert lifecycle.outcomes == ["retrying", "dead_letter", "completed", "cancelled", "completed"]


def test_restart_recovery_enqueues_same_staging_identity_once(staged_world):
    w, lifecycle, _calls = staged_world
    lifecycle.pending = [STAGING_ID]
    assert recover_gmail_attachment_jobs() == 1
    assert recover_gmail_attachment_jobs() == 1
    jobs = list(w.db.scalars(select(BackgroundJob)))
    assert len(jobs) == 1
    assert jobs[0].payload == {"staging_id": STAGING_ID}


def test_checksum_or_provider_size_tamper_is_denied_without_job(staged_world, monkeypatch):
    w, lifecycle, calls = staged_world
    fake = FakeGmail(calls, reported_size=len(SECRET_BODY) + 1)
    monkeypatch.setattr(
        gmail, "google_workspace_for_mailbox",
        lambda *_a, **_k: SimpleNamespace(service=lambda *_a, **_k: fake),
    )
    with pytest.raises(HTTPException) as exc:
        gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    assert exc.value.status_code == 422
    assert lifecycle.binding is None
    assert w.db.scalar(select(BackgroundJob)) is None


def test_missing_a05_adapter_denies_before_provider_download(staged_world):
    w, _lifecycle, calls = staged_world
    install_gmail_attachment_lifecycle(None)
    with pytest.raises(HTTPException) as exc:
        gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    assert exc.value.status_code == 503
    assert calls == []


def test_auto_mode_is_denied_before_provider_or_lifecycle(staged_world):
    w, lifecycle, calls = staged_world
    with pytest.raises(HTTPException) as exc:
        gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user, mode="AUTO")
    assert exc.value.status_code == 409
    assert calls == [] and lifecycle.binding is None


def test_job_payload_rejects_extra_content_and_handler_integrity_failure(staged_world):
    w, lifecycle, _calls, _ = _enqueue(staged_world)
    job = claim(w.db, "worker", lease_seconds=60)
    with execution_owner(job.id, "worker", attempt=job.attempts, locked_at=job.locked_at):
        with pytest.raises(GmailAttachmentDenied):
            run(job.kind, {"staging_id": STAGING_ID, "text": "forbidden"})
        lifecycle.fail_integrity = True
        with pytest.raises(GmailAttachmentIntegrityError):
            run(job.kind, dict(job.payload))
    assert lifecycle.process_reads == 1
