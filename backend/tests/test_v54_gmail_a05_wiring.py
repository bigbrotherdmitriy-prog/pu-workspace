from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api import gmail
from app.core.v54_permissions import SyntheticPolicy
from app.core.v54_refs import ObjectRef, VersionPin
from app.jobs.handlers import notify_outcome, run
from app.jobs.queue import cancel, claim, execution_owner, fail, succeed
from app.mailbox_identity.service import MailboxIdentityService
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.job import BackgroundJob
from app.models.mailbox_identity import MailboxAuthorityState, MailboxCutoverFlags
from app.models.materialization import Materialization
from app.models.v54_pilot import Evidence, SourceReference, SourceVersion
from app.staging.contracts import KekRef, StagingDescriptor
from app.staging.filesystem import FilesystemStagingStorage
from app.staging.gmail import (
    GmailAttachmentDenied,
    GmailAttachmentIntegrityError,
    install_gmail_attachment_lifecycle,
    recover_gmail_attachment_jobs,
)
from app.staging.gmail_a05 import (
    A05GmailAttachmentLifecycle,
    GmailAttachmentPolicyDecision,
)
from app.staging.lifecycle import LifecycleAuthority, MaterializationManifest
from test_v54_gmail_attachment_staging import FakeGmail, SECRET_BODY
from test_v54_mailbox_identity import command, world


class Keys:
    def resolve(self, reference, version):
        if (reference, version) != ("kms/gmail-test", "v1"):
            raise KeyError("unknown")
        return b"g" * 32


def _policy(w, now):
    tenant = {"kind": "int", "value": str(w.org.id)}
    policy_ref = ObjectRef(
        namespace="pu", type="policy", tenant_id=tenant,
        id={"kind": "uuid", "value": "90000000-0000-4000-8000-000000000001"},
    )
    policy = SyntheticPolicy(
        tenant_id=w.org.id, project_id=w.project.id,
        pin=VersionPin(ref=policy_ref, version_kind="revision", value=1),
        grants=frozenset((w.user.id, operation) for operation in (
            "write", "observe", "audit", "fragment",
        )),
        accounts=frozenset(), namespaces=frozenset(), binding_epochs=(),
        valid_until=now + timedelta(hours=2), freshness_ttl=timedelta(hours=1),
        authority_epoch=1, acl="allow", retention_known=True,
        residency_allowed=True, synthetic_only=True,
    )
    return LifecycleAuthority(
        policy=policy, allowed_residencies=frozenset({"eu-test"}),
        allowed_keks=frozenset({KekRef("kms/gmail-test", "v1")}),
        max_retention=timedelta(hours=2), copy_allowed=True,
        derive_allowed=True, retention_owner=True,
    )


@pytest.fixture
def a05_world(db_session, user_factory, monkeypatch, tmp_path):
    w = world(db_session, user_factory)
    MailboxIdentityService().reconcile(w.db, command(w), actor=w.user)
    flags = w.db.scalar(select(MailboxCutoverFlags))
    for field in ("shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions"):
        setattr(flags, field, True)
    w.message.attachments_json = (
        '[{"name":"never-persist-this-name.txt","mime_type":"text/plain","size":%d,'
        '"attachment_id":"provider-attachment-secret"}]' % len(SECRET_BODY)
    )
    w.db.commit()
    clock = [datetime.now(timezone.utc)]
    authority = _policy(w, clock[0])
    decisions = []

    def policy_factory(_db, binding):
        decision = GmailAttachmentPolicyDecision(
            authority=authority, residency="eu-test", retention=timedelta(hours=1),
            failed_retention=timedelta(minutes=15), kek=KekRef("kms/gmail-test", "v1"),
            allowed_mime_types=frozenset({"text/plain"}),
            derive_classes=frozenset({"document", "task", "draft", "risk", "decision"}),
            copy_allowed=True,
        )
        decisions.append((binding, decision))
        return decision

    storage = FilesystemStagingStorage(tmp_path / "ciphertext", Keys(), chunk_size=8)
    lifecycle = A05GmailAttachmentLifecycle(
        storage=storage, policy_factory=policy_factory, clock=lambda: clock[0],
        max_bytes=gmail.MAX_ATTACHMENT_BYTES,
    )
    install_gmail_attachment_lifecycle(lifecycle)
    calls = []
    fake = FakeGmail(calls)
    monkeypatch.setattr(
        gmail, "google_workspace_for_mailbox",
        lambda *_a, **_k: SimpleNamespace(service=lambda *_a, **_k: fake),
    )
    monkeypatch.setattr("app.database.SessionLocal", lambda: nullcontext(w.db))
    yield SimpleNamespace(
        **w.__dict__, lifecycle=lifecycle, storage=storage, provider=fake,
        provider_calls=calls, policy_calls=decisions, clock=clock,
    )
    install_gmail_attachment_lifecycle(None)


def _run_claimed(w):
    job = claim(w.db, "gmail-a05-worker", lease_seconds=120)
    assert job is not None
    with execution_owner(
        job.id, "gmail-a05-worker", attempt=job.attempts, locked_at=job.locked_at,
    ):
        result = run(job.kind, dict(job.payload))
    return job, result


def _descriptor(row):
    stored = MaterializationManifest.model_validate(row.manifest).storage
    return StagingDescriptor(
        object_id=stored.object_id, format_version=stored.format_version,
        chunk_size=stored.chunk_size,
        kek=KekRef(stored.kek_reference, stored.kek_version),
        wrapped_dek=stored.wrapped_dek,
    )


def test_real_a05_duplicate_stage_process_and_terminal_purge(a05_world):
    w = a05_world
    first = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    second = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    assert first["staging_id"] == second["staging_id"]
    assert first["job_id"] == second["job_id"]
    assert second["already_queued"] is True
    assert len(w.provider_calls) == 1

    row = w.db.scalar(select(Materialization))
    assert row.state == "DERIVED" and row.object_id != first["staging_id"]
    manifest = MaterializationManifest.model_validate(row.manifest)
    assert b"synthetic attachment content" == b"".join(
        w.storage.read_chunks(_descriptor(row), max_bytes=1024)
    )
    assert manifest.kind == "extracted_text"
    version = w.db.get(SourceVersion, row.source_version_id)
    binding = version.locator_at_observation["binding"]
    assert binding["mailbox_authority_version"] == 1
    serialized = str(version.locator_at_observation)
    assert "provider-attachment-secret" not in serialized
    assert "never-persist-this-name" not in serialized

    job, result = _run_claimed(w)
    assert result["document_id"] and result["status"] == "completed"
    assert succeed(w.db, job.id, "gmail-a05-worker", result)
    notify_outcome(job.kind, dict(job.payload), "completed")
    assert w.db.get(Materialization, row.id).state == "PURGED"
    document = w.db.get(Document, result["document_id"])
    assert document.external_id == f"gmail-staging:{first['staging_id']}"
    assert document.ocr_pages == 0 and document.ocr_review_status == "not_required"


def test_authority_version_rotation_denies_before_worker_ciphertext_read(a05_world):
    w = a05_world
    staged = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    job = claim(w.db, "gmail-a05-worker", lease_seconds=120)
    authority = w.db.scalar(select(MailboxAuthorityState))
    authority.authority_version += 1
    w.db.commit()
    with execution_owner(job.id, "gmail-a05-worker", attempt=job.attempts, locked_at=job.locked_at):
        with pytest.raises(GmailAttachmentDenied):
            run(job.kind, dict(job.payload))
    assert staged["staging_id"] and w.db.scalar(select(Document)) is None


def test_crash_before_enqueue_is_recovered_without_second_provider_read(a05_world, monkeypatch):
    w = a05_world
    original = gmail.enqueue_staged_gmail_attachment
    monkeypatch.setattr(
        gmail, "enqueue_staged_gmail_attachment",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("synthetic crash")),
    )
    with pytest.raises(RuntimeError):
        gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    w.db.rollback()
    assert w.db.scalar(select(Materialization)).state == "DERIVED"
    monkeypatch.setattr(gmail, "enqueue_staged_gmail_attachment", original)
    assert recover_gmail_attachment_jobs() == 1
    job = w.db.scalar(select(BackgroundJob))
    assert job.payload == {"staging_id": job.payload["staging_id"]}
    assert len(w.provider_calls) == 1


def test_integrity_failure_publishes_no_document_and_purges(a05_world):
    w = a05_world
    staged = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    row = w.db.scalar(select(Materialization))
    descriptor = _descriptor(row)
    path = w.storage._path(descriptor.object_id)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    job = claim(w.db, "gmail-a05-worker", lease_seconds=120)
    with execution_owner(job.id, "gmail-a05-worker", attempt=job.attempts, locked_at=job.locked_at):
        with pytest.raises(GmailAttachmentIntegrityError) as error:
            run(job.kind, dict(job.payload))
    assert w.db.scalar(select(Document)) is None
    status = fail(w.db, job.id, "gmail-a05-worker", error.value, retryable=False)
    assert status == "failed"
    notify_outcome(job.kind, {"staging_id": staged["staging_id"]}, status)
    assert w.db.get(Materialization, row.id).state == "PURGED"


def test_jobs_and_audits_never_persist_provider_or_crypto_material(a05_world):
    w = a05_world
    result = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    job = w.db.scalar(select(BackgroundJob))
    assert job.payload == {"staging_id": result["staging_id"]}
    audit_text = " ".join(
        str((item.action, item.entity_type, item.entity_id, item.details))
        for item in w.db.scalars(select(AuditLog))
    )
    forbidden = (
        "provider-attachment-secret", "provider-message-synthetic",
        "never-persist-this-name", SECRET_BODY.decode(), "kms/gmail-test", "sha256",
    )
    assert not any(value in str(job.payload) or value in audit_text for value in forbidden)


def test_cancel_cleanup_and_out_of_order_hooks_are_idempotent(a05_world):
    w = a05_world
    staged = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    job = w.db.get(BackgroundJob, staged["job_id"])
    assert cancel(w.db, job.id)
    notify_outcome(job.kind, dict(job.payload), "cancelled")
    row = w.db.scalar(select(Materialization))
    assert row.state == "PURGED"
    notify_outcome(job.kind, dict(job.payload), "cancelled")
    notify_outcome(job.kind, dict(job.payload), "completed")
    assert w.db.get(Materialization, row.id).state == "PURGED"


def test_failed_ciphertext_is_retained_only_to_failed_policy_deadline(a05_world):
    w = a05_world
    staged = gmail.import_gmail_attachment(w.message.id, 0, w.db, w.user)
    job = claim(w.db, "gmail-a05-worker", lease_seconds=120)
    assert fail(w.db, job.id, "gmail-a05-worker", "synthetic", retryable=False) == "failed"
    notify_outcome(job.kind, dict(job.payload), "failed")
    row = w.db.scalar(select(Materialization))
    assert row.state == "DERIVED"
    w.clock[0] += timedelta(minutes=16)
    assert recover_gmail_attachment_jobs() == 0
    assert w.db.get(Materialization, row.id).state == "PURGED"
    assert staged["staging_id"] not in str(w.db.get(BackgroundJob, job.id).last_error)
