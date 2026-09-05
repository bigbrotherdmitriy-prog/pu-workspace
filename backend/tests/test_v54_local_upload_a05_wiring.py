"""Synthetic integration of local upload with the real a05 lifecycle."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import Session, sessionmaker

import app.models
import app.local_upload_staging as local_staging
from app.database import Base
from app.jobs.queue import claim, execution_owner, recover_expired
from app.local_upload_staging import (
    LocalUploadCancelled, LocalUploadLifecycleAdapter, LocalUploadRuntime,
    UploadCandidate, UploadScope,
    configure_local_upload_runtime, run_local_upload_job, stage_and_enqueue,
)
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.materialization import Materialization
from app.models.v54_pilot import AuditExtension, Evidence, SourceReference, SourceVersion
from app.staging.contracts import KekRef
from app.staging.filesystem import FilesystemStagingStorage
from app.staging.lifecycle import LifecycleAuthority
from app.staging.local_upload import A05LocalUploadLifecycle
from test_v54_source_evidence_pilot import policy as source_policy
from v54_pilot_fixture import seed


class Keys:
    def resolve(self, reference, version):
        if (reference, version) != ("local-upload", "v1"):
            raise KeyError("unknown")
        return b"l" * 32


class Processor:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = 0

    def process(self, session, *, record, content, operation_key):
        self.calls += 1
        if self.error:
            raise self.error
        assert content == b"synthetic confidential body"
        assert operation_key == f"local-upload:{record.staging_id}"
        return {"processed": 1, "skipped": 0, "tasks": 0, "risks": 0,
                "decisions": 0, "drafts": 0, "documents": []}


@pytest.fixture
def wired(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as db:
        seed(db)
    now = datetime.now(timezone.utc)
    policy = source_policy()
    policy = replace(
        policy, valid_until=now + timedelta(hours=2),
        grants=policy.grants | frozenset({(2, "fragment")}),
    )
    authority = LifecycleAuthority(
        policy=policy, allowed_residencies=frozenset({"local-test"}),
        allowed_keks=frozenset({KekRef("local-upload", "v1")}),
        max_retention=timedelta(hours=1), derive_allowed=True,
        retention_owner=True,
    )
    storage = FilesystemStagingStorage(tmp_path / "ciphertext", Keys(), chunk_size=16)

    def authority_factory(db, upload_scope):
        assert upload_scope == UploadScope(2, 4)
        return authority

    backend = A05LocalUploadLifecycle(
        storage=storage, authority_factory=authority_factory,
        clock=lambda: datetime.now(timezone.utc), residency="local-test",
        kek=KekRef("local-upload", "v1"), max_file_bytes=1024,
    )
    processor = Processor()
    runtime = LocalUploadRuntime(
        storage=storage, lifecycle=LocalUploadLifecycleAdapter(backend),
        processor=processor, session_factory=sessions,
        kek=KekRef("local-upload", "v1"), max_file_bytes=1024,
        allowed_mime_types=frozenset({"text/plain", "application/pdf"}),
        retention=timedelta(minutes=30),
    )
    configure_local_upload_runtime(runtime)
    try:
        yield engine, sessions, runtime, processor, backend, tmp_path
    finally:
        configure_local_upload_runtime(None)
        engine.dispose()


def _stage(sessions):
    with sessions() as db:
        db.begin()
        return stage_and_enqueue(
            db, runtime=local_staging.get_local_upload_runtime(),
            scope=UploadScope(2, 4),
            candidate=UploadCandidate(
                "confidential.txt", "text/plain", b"synthetic confidential body",
            ),
            request_key="synthetic-request", index=0,
        )


def _claimed(sessions, worker):
    with sessions() as db:
        row = claim(db, worker, lease_seconds=300)
        assert row is not None
        return row.id, row.worker_id, row.attempts, row.locked_at


def test_real_a05_source_version_materialization_queue_and_cleanup(wired):
    _, sessions, _, processor, _, tmp_path = wired
    queued = _stage(sessions)
    same = _stage(sessions)
    assert same.staging_id == queued.staging_id and same.job_id == queued.job_id
    assert same.status == "already_queued"

    with sessions() as db:
        job = db.get(BackgroundJob, queued.job_id)
        row = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        version = db.get(SourceVersion, row.source_version_id)
        source = db.get(SourceReference, row.source_id)
        evidence = db.get(Evidence, row.evidence_id)
        assert job.payload == {"staging_id": queued.staging_id}
        assert row.owner_id == 2 and row.project_id == 4 and row.state == "DERIVED"
        assert source.origin_project_id == 4 and version.source_id == source.id
        assert evidence.source_version_id == version.id
        assert evidence.representation_ref["representation_id"] == row.id
        assert evidence.representation_ref["handle"] == row.object_id
        assert evidence.representation_ref["source_version_pin"]["ref"]["id"]["value"] == version.id

    claim_snapshot = _claimed(sessions, "worker-a")
    with execution_owner(
        claim_snapshot[0], claim_snapshot[1], attempt=claim_snapshot[2],
        locked_at=claim_snapshot[3],
    ):
        result = run_local_upload_job({"staging_id": queued.staging_id})
    assert result["processed"] == 1 and processor.calls == 1
    assert not list((tmp_path / "ciphertext").rglob("*.enc"))

    with sessions() as db:
        row = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        assert row.state == "PURGED"
        assert row.manifest["outcome"] == "completed"
        audit_rows = list(db.scalars(select(AuditLog)))
        extensions = list(db.scalars(select(AuditExtension)))
        rendered = repr([(x.action, x.entity_type, x.details) for x in audit_rows])
        rendered += repr([(x.subject_id, x.subject_pin, x.correlation_id) for x in extensions])
        for forbidden in (
            "synthetic confidential body", "confidential.txt", "content_base64",
            version.integrity[0]["value"], "local-upload:v1",
        ):
            assert forbidden not in rendered


def test_restart_and_expired_lease_cannot_read_then_new_claim_recovers(wired):
    _, sessions, runtime, processor, backend, _ = wired
    queued = _stage(sessions)
    old = _claimed(sessions, "worker-old")
    with sessions.begin() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
    with sessions() as db:
        assert recover_expired(db) == 1
    new = _claimed(sessions, "worker-new")

    # Recreate composition to prove all scope/source/representation state is durable.
    recreated = replace(runtime, lifecycle=LocalUploadLifecycleAdapter(
        backend,
    ))
    configure_local_upload_runtime(recreated)
    with execution_owner(old[0], old[1], attempt=old[2], locked_at=old[3]):
        with pytest.raises(local_staging.LocalUploadUnavailable):
            run_local_upload_job({"staging_id": queued.staging_id})
    assert processor.calls == 0
    with execution_owner(new[0], new[1], attempt=new[2], locked_at=new[3]):
        assert run_local_upload_job({"staging_id": queued.staging_id})["processed"] == 1
    assert processor.calls == 1


def test_failed_processing_retains_ciphertext_and_materialization(wired):
    _, sessions, runtime, _, _, tmp_path = wired
    queued = _stage(sessions)
    failing = Processor(error=RuntimeError("SECRET path=C:/private checksum=raw key=raw"))
    configure_local_upload_runtime(replace(runtime, processor=failing))
    live = _claimed(sessions, "worker-fail")
    with execution_owner(live[0], live[1], attempt=live[2], locked_at=live[3]):
        with pytest.raises(RuntimeError):
            run_local_upload_job({"staging_id": queued.staging_id})
    with sessions() as db:
        row = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        assert row.state == "DERIVED"
    assert list((tmp_path / "ciphertext").rglob("*.enc"))


def test_cancelled_processing_purges_real_materialization(wired):
    _, sessions, runtime, _, _, tmp_path = wired
    queued = _stage(sessions)
    configure_local_upload_runtime(replace(
        runtime, processor=Processor(error=LocalUploadCancelled("cancelled")),
    ))
    live = _claimed(sessions, "worker-cancel")
    with execution_owner(live[0], live[1], attempt=live[2], locked_at=live[3]):
        assert run_local_upload_job({"staging_id": queued.staging_id}) == {
            "cancelled": True, "staging_id": queued.staging_id,
        }
    with sessions() as db:
        row = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        assert row.state == "PURGED" and row.manifest["outcome"] == "cancelled"
    assert not list((tmp_path / "ciphertext").rglob("*.enc"))


def test_restart_finishes_cleanup_after_durable_finalize(wired):
    _, sessions, runtime, processor, backend, tmp_path = wired
    queued = _stage(sessions)
    live = _claimed(sessions, "worker-crash")
    summary = {"processed": 1, "skipped": 0, "tasks": 0, "risks": 0,
               "decisions": 0, "drafts": 0, "documents": []}
    with sessions() as db:
        loaded = backend.load_for_processing(
            db, staging_id=queued.staging_id, job_id=queued.job_id, claim=live,
        )
        db.commit()  # authorization before the (simulated) read
        backend.finalize(
            db, scope=loaded.scope, staging_id=queued.staging_id,
            job_id=queued.job_id, claim=live, outcome="completed", result=summary,
        )
        db.commit()  # crash now: ciphertext remains, terminal decision is durable

    assert list((tmp_path / "ciphertext").rglob("*.enc"))
    configure_local_upload_runtime(replace(
        runtime, lifecycle=LocalUploadLifecycleAdapter(backend),
    ))
    with execution_owner(live[0], live[1], attempt=live[2], locked_at=live[3]):
        result = run_local_upload_job({"staging_id": queued.staging_id})
    assert result == {"staging_id": queued.staging_id, **summary}
    assert processor.calls == 0
    assert not list((tmp_path / "ciphertext").rglob("*.enc"))
    with sessions() as db:
        assert db.get(Materialization, str(UUID(hex=queued.staging_id))).state == "PURGED"
