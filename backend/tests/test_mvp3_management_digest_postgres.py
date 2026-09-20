"""Opt-in PostgreSQL concurrency, lease-recovery and migration gates for M3-07."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from threading import Barrier
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.database import Base
from app.jobs.queue import claim, enqueue, recover_expired, succeed
from app.management_digest import install_digest_runtime, run_digest_job
from app.models.job import BackgroundJob
from app.models.management import ManagementDigest, Notification, NotificationPolicy, Obligation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.schema import CURRENT_SCHEMA_REVISION

BACKEND = Path(__file__).resolve().parents[1]


def _postgres_url():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 digest gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    return url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url


@pytest.fixture
def pg_digest_world():
    base = create_engine(_postgres_url(), connect_args={"connect_timeout": 5})
    schema = "mvp3_digest_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db:
        organization = Organization(name="Digest PG tenant")
        db.add(organization); db.flush()
        user = User(name="Digest PG user", email=f"digest-{uuid4().hex}@example.test")
        db.add(user); db.flush()
        project = Project(name="Digest PG project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        policy = NotificationPolicy(
            organization_id=organization.id, project_id=project.id, user_id=user.id,
            timezone="Europe/Moscow", quiet_start=time(22), quiet_end=time(7),
            channels=["in_app"], enabled=True, digest_enabled=True,
            digest_cadence="daily", digest_local_time=time(9),
        )
        db.add(policy); db.flush()
        db.add(Obligation(
            project_id=project.id, owner_user_id=user.id, title="PG obligation",
            status="confirmed", due_date=date(2026, 9, 30), source_type="document",
            source_id="source-pg", source_name="source", source_excerpt="private",
            source_hash="a" * 64, confidence=1.0,
        ))
        ids = organization.id, project.id, user.id, policy.id, policy.record_version
    try:
        yield sessions, ids
    finally:
        install_digest_runtime()
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def _payload(ids):
    organization_id, project_id, user_id, policy_id, version = ids
    return {
        "organization_id": organization_id, "project_id": project_id,
        "user_id": user_id, "policy_id": policy_id,
        "policy_record_version": version, "local_date": "2026-09-21",
    }


def test_postgres_concurrent_digest_workers_create_one_receipt(pg_digest_world):
    sessions, ids = pg_digest_world
    barrier = Barrier(2)

    def factory():
        return sessions()

    install_digest_runtime(factory, clock=lambda: datetime(2026, 9, 21, 8, tzinfo=timezone.utc))

    def run():
        barrier.wait(timeout=10)
        return run_digest_job(_payload(ids))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=25) for future in (pool.submit(run), pool.submit(run))]
    assert sorted(result["status"] for result in results) == ["already_created", "created"]
    with sessions() as db:
        assert db.query(ManagementDigest).count() == 1
        assert db.query(Notification).filter_by(kind="management_digest").count() == 1


def test_postgres_expired_worker_recovery_replays_safely(pg_digest_world):
    sessions, ids = pg_digest_world
    payload = _payload(ids)
    with sessions() as db:
        queued = enqueue(db, "mvp3.management_digest", payload,
                         idempotency_key="digest-restart-test", max_attempts=3)
        job_id = queued.id
    with sessions() as db:
        first = claim(db, "worker-before-crash", lease_seconds=60)
        assert first and first.id == job_id and first.status == "running"
        db.execute(update(BackgroundJob).where(BackgroundJob.id == job_id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        db.commit()
    with sessions() as db:
        assert recover_expired(db) == 1
        recovered = claim(db, "worker-after-restart", lease_seconds=60)
        assert recovered and recovered.id == job_id and recovered.attempts == 2

    install_digest_runtime(sessions, clock=lambda: datetime(2026, 9, 21, 8, tzinfo=timezone.utc))
    result = run_digest_job(payload)
    with sessions() as db:
        assert succeed(db, job_id, "worker-after-restart", result)
        assert db.get(BackgroundJob, job_id).status == "completed"
        assert db.query(ManagementDigest).count() == 1
        assert db.query(Notification).filter_by(kind="management_digest").count() == 1


def _migration_config():
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_postgres_migration_preserves_existing_notification_policy(monkeypatch):
    base_url = _postgres_url()
    base = create_engine(base_url, connect_args={"connect_timeout": 5})
    schema = "mvp3_digest_migration_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    schema_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", schema_url.render_as_string(hide_password=False))
    config = _migration_config()
    try:
        command.upgrade(config, "b85e7f9a1d23")
        engine = create_engine(schema_url, connect_args={"connect_timeout": 5})
        with Session(engine) as db:
            organization = Organization(name="Migration digest tenant")
            db.add(organization); db.flush()
            user = User(name="Migration digest user", email=f"migration-{uuid4().hex}@example.test")
            db.add(user); db.flush()
            project = Project(name="Migration digest project", organization_id=organization.id)
            db.add(project); db.flush()
            # New mapped attributes are omitted from INSERT because this schema
            # intentionally represents the preceding revision.
            db.execute(text(
                "INSERT INTO notification_policies "
                "(organization_id,project_id,user_id,timezone,deadline_local_time,quiet_start,quiet_end,"
                "escalation_delays,channels,enabled,record_version) "
                "VALUES (:o,:p,:u,'Europe/Moscow','09:00','22:00','07:00',CAST('[0]' AS json),"
                "CAST('[\"in_app\"]' AS json),true,1)"
            ), {"o": organization.id, "p": project.id, "u": user.id})
            db.commit()
        engine.dispose()

        command.upgrade(config, CURRENT_SCHEMA_REVISION)
        engine = create_engine(schema_url, connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
            row = connection.execute(text(
                "SELECT digest_enabled,digest_cadence,digest_local_time FROM notification_policies"
            )).one()
            assert row[0] is False and row[1] == "daily" and str(row[2]).startswith("09:00")
            assert "management_digests" in inspect(connection).get_table_names()
        engine.dispose()
    finally:
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()
