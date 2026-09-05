from datetime import timedelta
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.jobs.handlers import run
from app.database import Base
from app.jobs.queue import claim, enqueue, execution_owner, recover_expired, succeed, utcnow
from app.models.job import BackgroundJob
from app.models.organizer import OrganizerOperation
from app.organizer_engine.storage_mutation_jobs import install_runtime
from app.organizer_engine.storage_mutation_repository import StorageMutationResolver
from app.organizer_engine.storage_mutation_runtime import DurableMutationLedger, SyntheticStorageMutationRuntime
from test_mvp1_storage_mutation_repository import world
from test_mvp1_storage_mutation_runtime import SyntheticAdapter


pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_DSN"), reason="TEST_POSTGRES_DSN unavailable")


@pytest.fixture
def isolated_postgres_engine():
    """Model-level runtime proof in an owned schema, never a production database."""
    url = os.environ["TEST_POSTGRES_DSN"]
    parsed = make_url(url)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db"} or (
        parsed.host == "postgres" and os.getenv("GITHUB_ACTIONS") == "true"
    )
    assert parsed.database == "puw_storage_ci" or (parsed.database or "").startswith("puw_v54_test_")
    assert not parsed.query
    schema = "storage_mutation_test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    engine = None
    created = False
    try:
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        engine = create_engine(url, hide_parameters=True, connect_args={
            "connect_timeout": 5,
            "options": f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=15000",
        })
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_resolver_serializes_overlapping_transactions(isolated_postgres_engine):
    sessions = sessionmaker(isolated_postgres_engine, expire_on_commit=False)
    with sessions.begin() as db:
        project, _connection, _snapshot, action = world(db)
        payload = {"project_id": project.id, "proposal_id": action.proposal_id,
                   "action_id": action.id, "command_key": "storage-pg-lock-01",
                   "expected_record_version": 1}
    with sessions() as first, sessions() as second:
        command = StorageMutationResolver(first).resolve(payload)
        second.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError) as blocked:
            StorageMutationResolver(second).resolve(payload)
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
        second.rollback()
        first.rollback()
        replay = StorageMutationResolver(second).resolve(payload)
        assert replay == command


def test_postgres_worker_crash_reconciles_without_double_provider_effect(isolated_postgres_engine):
    engine = isolated_postgres_engine
    sessions = sessionmaker(engine, expire_on_commit=False)
    adapter = SyntheticAdapter()
    try:
        with sessions() as db:
            project, connection, _snapshot, action = world(db)
            connection.connection_id = "synthetic:postgres-worker"
            binding = dict(_snapshot.analysis_result["storage_binding"]); binding["connection_id"] = connection.connection_id
            _snapshot.analysis_result = {"storage_binding": binding}
            db.commit()
            payload = {"project_id": project.id, "proposal_id": action.proposal_id, "action_id": action.id,
                       "command_key": "storage-pg-crash-01", "expected_record_version": 1, "operation": "apply"}
            job = enqueue(db, "workspace.storage_mutation", payload,
                          idempotency_key="storage-pg-crash-job-01")
            job_id = job.id
        adapter.item.id = "root/nested/file"; adapter.item.parent_id = "root/nested"
        install_runtime(SyntheticStorageMutationRuntime(sessions, lambda _pin: adapter, enabled=True))

        with sessions() as db:
            first = claim(db, "storage-worker-one", lease_seconds=60)
            assert first and first.id == job_id
            command = StorageMutationResolver(db).resolve(payload)
            DurableMutationLedger(db, payload, command).append_attempt(command)
            db.commit()
        # Synthetic fault boundary: provider committed, worker died before receipt/job success.
        adapter.rename_file("root/nested/file", "standard.pdf", "root/nested")
        with sessions() as db:
            row = db.get(BackgroundJob, job_id); row.lease_expires_at = utcnow() - timedelta(seconds=1); db.commit()
            assert recover_expired(db) == 1
            second = claim(db, "storage-worker-two", lease_seconds=60)
            assert second and second.id == job_id and second.worker_id == "storage-worker-two"
            owner = (second.id, second.worker_id, second.attempts, second.locked_at)
        with execution_owner(*owner):
            result = run("workspace.storage_mutation", payload)
        with sessions() as db:
            assert succeed(db, job_id, "storage-worker-two", result)
            row = db.get(BackgroundJob, job_id)
            assert row.status == "completed" and result["outcome"] == "applied"
            assert db.scalar(select(func.count()).select_from(OrganizerOperation).where(
                OrganizerOperation.proposal_id == action.proposal_id,
                OrganizerOperation.op_type == "storage_mutation_receipt")) == 1
        assert adapter.calls == 1
    finally:
        install_runtime(None)
