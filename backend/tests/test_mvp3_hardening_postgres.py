"""Opt-in real PostgreSQL gate for MVP3 lost-update protection."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, datetime, time, timezone
import os
from threading import Event, local
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import management as management_api
from app.api import tasks as tasks_api
from app.database import Base
from app.models.job import BackgroundJob
from app.models.management import ManagementHistory, Notification, NotificationPolicy, Obligation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User


@pytest.fixture
def mvp3_pg_engine():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 concurrency gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    base = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "mvp3_cas_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def test_postgres_two_task_updates_have_exactly_one_cas_winner(mvp3_pg_engine, monkeypatch):
    with Session(mvp3_pg_engine) as db:
        organization = Organization(name="MVP3 PG tenant")
        user = User(name="Manager", email=f"mvp3-{uuid4().hex}@example.test", is_admin=False)
        db.add_all([organization, user]); db.flush()
        project = Project(name="MVP3 PG project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        task = Task(project_id=project.id, assignee_user_id=user.id, created_by_user_id=user.id,
                    title="Concurrent task", status="assigned", priority="normal", source_type="manual",
                    source_file_id="pg-source", source_file_name="source.txt", source_excerpt="evidence",
                    source_excerpt_hash="9" * 64, confidence=1.0)
        db.add(task); db.commit()
        task_id, user_id = task.id, user.id

    locked, release, second_started = Event(), Event(), Event()
    thread_state = local()
    original = tasks_api._locked_versioned

    def lock_with_barrier(db, model, entity_id, expected, label):
        item = original(db, model, entity_id, expected, label)
        if getattr(thread_state, "first", False):
            locked.set()
            assert release.wait(10)
        return item

    monkeypatch.setattr(tasks_api, "_locked_versioned", lock_with_barrier)

    def update(first):
        thread_state.first = first
        try:
            with Session(mvp3_pg_engine) as db:
                if not first:
                    second_started.set()
                return tasks_api.update_task(
                    task_id, tasks_api.TaskUpdate(status="in_progress", expected_record_version=1),
                    db, db.get(User, user_id),
                )["record_version"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(update, True)
        assert locked.wait(10)
        second = pool.submit(update, False)
        assert second_started.wait(5)
        with pytest.raises(TimeoutError):
            second.result(timeout=0.2)
        release.set()
        assert sorted([first.result(timeout=10), second.result(timeout=10)]) == [2, 409]

    with Session(mvp3_pg_engine) as db:
        assert db.get(Task, task_id).record_version == 2
        assert db.scalar(select(func.count()).select_from(ManagementHistory).where(
            ManagementHistory.entity_type == "task", ManagementHistory.entity_id == task_id,
        )) == 1


def test_postgres_concurrent_notification_refresh_is_idempotent(mvp3_pg_engine, monkeypatch):
    now = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(management_api, "_utcnow", lambda: now)
    with Session(mvp3_pg_engine) as db:
        organization = Organization(name="MVP3 notification tenant")
        user = User(name="Manager", email=f"notify-{uuid4().hex}@example.test", is_admin=False)
        db.add_all([organization, user]); db.flush()
        project = Project(name="MVP3 notification project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        db.add(NotificationPolicy(
            organization_id=organization.id, project_id=project.id, user_id=user.id,
            timezone="Europe/Moscow", deadline_local_time=time(22, 30),
            quiet_start=time(22), quiet_end=time(7), escalation_delays=[0, 60],
            channels=["in_app"],
        ))
        obligation = Obligation(
            project_id=project.id, owner_user_id=user.id, title="Concurrent deadline",
            status="confirmed", due_date=date(2026, 9, 4), source_type="manual",
            source_id="concurrent-deadline", source_name="source.txt", source_excerpt="evidence",
            source_hash="e" * 64, confidence=1.0,
        )
        db.add(obligation); db.commit()
        project_id, user_id = project.id, user.id

    def refresh():
        with Session(mvp3_pg_engine) as db:
            return management_api.refresh_notifications(project_id, db, db.get(User, user_id))["unread"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in [pool.submit(refresh), pool.submit(refresh)]]
    assert results == [1, 1]

    with Session(mvp3_pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 1
        assert db.scalar(select(func.count()).select_from(BackgroundJob).where(
            BackgroundJob.kind == "notifications.escalation.proposal",
        )) == 2
