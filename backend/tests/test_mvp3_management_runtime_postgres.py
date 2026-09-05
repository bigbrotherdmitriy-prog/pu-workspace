"""Opt-in PostgreSQL restart/replay proof for the MVP3 digest path.

The test accepts only an explicitly named disposable database, creates a
random schema, and never invokes a provider adapter.  Closing and recreating
the SQLAlchemy engine models the persistence boundary of an API/worker
restart without depending on the lifetime of either process.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.jobs import queue
from app.models.job import BackgroundJob
from app.models.management import Notification
from app.models.management_digest import ManagementDigestPreference
from app.models.project_member import ProjectMember
from app.models.v54_pilot import SourceReference
from app.models.v54_provider_action import ProviderAction
from app.mvp3.lifecycle import ManagementLifecycle
from app.mvp3.meeting_digest import (
    DigestPreference,
    DigestPreferenceService,
    install_digest_runtime,
    run_digest_job,
    schedule_digest_jobs,
)
from v54_pilot_fixture import pin, seed, uid


def _database_url() -> str:
    value = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: explicit isolated MVP3 PostgreSQL URL not supplied")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1"} or (
        os.getenv("GITHUB_ACTIONS") == "true" and parsed.host == "postgres"
    )
    assert parsed.database and parsed.database.startswith("puw_mvp3_test_")
    assert not parsed.query, "Connection options must not redirect the test database"
    return value


def _engine(url: str, schema: str):
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def isolate(connection, _record):
        cursor = connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.execute("SET lock_timeout TO '8s'")
        cursor.execute("SET statement_timeout TO '20s'")
        cursor.close()
        connection.commit()

    return engine


@pytest.fixture()
def postgres_world():
    url = _database_url()
    schema = "mvp3_runtime_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    created = False
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        engine = _engine(url, schema)
        Base.metadata.create_all(engine)
        yield url, schema, engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _seed_digest(engine) -> tuple[int, int]:
    with Session(engine) as db, db.begin():
        seed(db)
        source = db.get(SourceReference, uid(13))
        source.availability = "available"
        source.freshness = "fresh"
        source.sync_state = "current"
        db.add(ProjectMember(project_id=4, user_id=2, role="manager"))
        scope = ManagementLifecycle().scope(db, project_id=4, actor_user_id=2)
        ManagementLifecycle().create_obligation(
            db,
            scope=scope,
            title="Synthetic restart-safe obligation",
            owner_user_id=2,
            evidence_pins=[pin("evidence", uid(16), tenant=1)],
            due_date=date(2026, 9, 1),
        )
        preference = DigestPreferenceService().put(
            db,
            project_id=4,
            user_id=2,
            expected_version=0,
            preference=DigestPreference(
                timezone="Europe/Moscow",
                quiet_start=time(20),
                quiet_end=time(8),
                channel="in_app",
                cadence="weekdays",
            ),
        )
        db.flush()
        return preference.id, preference.record_version


def test_postgresql_digest_is_single_after_scheduler_race_restart_and_replay(postgres_world):
    url, schema, engine = postgres_world
    preference_id, preference_version = _seed_digest(engine)
    now = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
    barrier = Barrier(2, timeout=10)

    def schedule_once() -> int:
        with Session(engine) as db:
            barrier.wait()
            return schedule_digest_jobs(db, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        scheduled = list(executor.map(lambda _index: schedule_once(), (1, 2)))
    assert sum(scheduled) >= 1
    with Session(engine) as db:
        jobs = db.scalars(select(BackgroundJob).where(
            BackgroundJob.kind == "mvp3.management_digest",
        )).all()
        assert len(jobs) == 1
        job_id = jobs[0].id
        assert jobs[0].payload == {
            "project_id": 4,
            "user_id": 2,
            "local_date": "2026-09-07",
            "preference_id": preference_id,
            "preference_version": preference_version,
        }

    # First worker claims and then disappears before any business effect.
    with Session(engine) as db:
        first = queue.claim(db, "mvp3-worker-before-restart", lease_seconds=60)
        assert first is not None and first.id == job_id
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE background_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=:job_id"
        ), {"job_id": job_id})
    engine.dispose()

    # A fresh engine/session represents restarted API, scheduler, and worker
    # processes reading the same durable PostgreSQL state.
    restarted = _engine(url, schema)
    sessions: list[Session] = []
    try:
        with Session(restarted) as db:
            assert schedule_digest_jobs(db, now=now) == 0
            assert queue.recover_expired(db) == 1
            second = queue.claim(db, "mvp3-worker-after-restart", lease_seconds=60)
            assert second is not None and second.id == job_id
            assert second.attempts == 2
            payload = dict(second.payload)

        def session_factory() -> Session:
            session = Session(restarted)
            sessions.append(session)
            return session

        install_digest_runtime(session_factory, clock=lambda: now)
        try:
            completed = run_digest_job(payload)
            replay = run_digest_job(payload)
        finally:
            install_digest_runtime()
            for session in sessions:
                session.close()

        assert completed["status"] == "created"
        assert replay["status"] == "already_created"
        assert completed["external_actions_created"] is False
        assert replay["external_actions_created"] is False
        with Session(restarted) as db:
            assert queue.succeed(db, job_id, "mvp3-worker-before-restart", completed) is False
            assert queue.succeed(db, job_id, "mvp3-worker-after-restart", completed) is True
        with Session(restarted) as db:
            job = db.get(BackgroundJob, job_id)
            assert job.status == "completed" and job.attempts == 2
            assert db.scalar(select(func.count()).select_from(Notification).where(
                Notification.kind == "management_digest",
            )) == 1
            assert db.scalar(select(func.count()).select_from(ProviderAction)) == 0
            assert db.scalar(select(func.count()).select_from(ManagementDigestPreference)) == 1
    finally:
        install_digest_runtime()
        restarted.dispose()
