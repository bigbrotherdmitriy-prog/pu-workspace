"""Opt-in PostgreSQL lease/concurrency proof for local-upload a05 wiring."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier, Lock, Thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models
from app.database import Base
from app.core.v54_permissions import SourceEvidenceError
from app.jobs.queue import claim, recover_expired
from app.local_upload_staging import (
    LocalUploadLifecycleAdapter, LocalUploadRuntime, UploadCandidate, UploadScope,
    stage_and_enqueue,
)
from app.models.job import BackgroundJob
from app.staging.contracts import KekRef
from app.staging.filesystem import FilesystemStagingStorage
from app.staging.lifecycle import LifecycleAuthority
from app.staging.local_upload import A05LocalUploadLifecycle
from test_v54_local_upload_a05_wiring import Keys, Processor
from test_v54_source_evidence_pilot import policy as source_policy
from v54_pilot_fixture import seed


def safe_url():
    value = os.getenv("PUW_V54_LOCAL_UPLOAD_DATABASE_URL") or os.getenv(
        "PUW_V54_MATERIALIZATION_DATABASE_URL",
    )
    if not value:
        pytest.skip("CONDITIONAL: local-upload PostgreSQL URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_v54_test_") and not parsed.query
    return value


def test_postgres_only_current_lease_can_authorize_materialization_read(tmp_path):
    url = safe_url()
    schema = "v54_local_upload_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, hide_parameters=True, connect_args={
        "connect_timeout": 5,
        "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000",
    })
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as db:
            seed(db)
        now = datetime.now(timezone.utc)
        base = source_policy()
        policy = replace(
            base, valid_until=now + timedelta(hours=2),
            grants=base.grants | frozenset({(2, "fragment")}),
        )
        authority = LifecycleAuthority(
            policy=policy, allowed_residencies=frozenset({"local-test"}),
            allowed_keks=frozenset({KekRef("local-upload", "v1")}),
            max_retention=timedelta(hours=1), derive_allowed=True,
            retention_owner=True,
        )
        storage = FilesystemStagingStorage(tmp_path / "ciphertext", Keys(), chunk_size=16)
        backend = A05LocalUploadLifecycle(
            storage=storage, authority_factory=lambda db, scope: authority,
            clock=lambda: datetime.now(timezone.utc), residency="local-test",
            kek=KekRef("local-upload", "v1"), max_file_bytes=1024,
        )
        runtime = LocalUploadRuntime(
            storage=storage, lifecycle=LocalUploadLifecycleAdapter(backend),
            processor=Processor(), session_factory=sessions,
            kek=KekRef("local-upload", "v1"), max_file_bytes=1024,
            allowed_mime_types=frozenset({"text/plain"}),
            retention=timedelta(minutes=30),
        )
        with sessions() as db:
            db.begin()
            queued = stage_and_enqueue(
                db, runtime=runtime, scope=UploadScope(2, 4),
                candidate=UploadCandidate("lease.txt", "text/plain",
                                          b"synthetic confidential body"),
                request_key="postgres-lease", index=0,
            )
        with sessions() as db:
            old = claim(db, "worker-old", lease_seconds=300)
            old_claim = (old.id, old.worker_id, old.attempts, old.locked_at)
        with sessions.begin() as db:
            db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ))
        with sessions() as db:
            assert recover_expired(db) == 1
            current = claim(db, "worker-current", lease_seconds=300)
            current_claim = (current.id, current.worker_id, current.attempts, current.locked_at)

        barrier = Barrier(2)
        lock = Lock()
        outcomes = []

        def contender(snapshot):
            try:
                with sessions() as db:
                    barrier.wait(5)
                    backend.load_for_processing(
                        db, staging_id=queued.staging_id, job_id=queued.job_id,
                        claim=snapshot,
                    )
                outcome = "authorized"
            except SourceEvidenceError:
                outcome = "denied"
            with lock:
                outcomes.append(outcome)

        threads = [Thread(target=contender, args=(snapshot,))
                   for snapshot in (old_claim, current_claim)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert not any(thread.is_alive() for thread in threads)
        assert sorted(outcomes) == ["authorized", "denied"]
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
