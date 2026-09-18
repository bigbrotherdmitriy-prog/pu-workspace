"""Real PostgreSQL race and lease-fencing acceptance for product outbox."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register mapped tables
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.models.v54_provider_action import ProviderAction, ProviderActionApproval, ProviderDispatchOutbox
from app.provider_actions.contracts import ProviderActionError
from app.provider_actions.product import build_product_runtime, queue_confirmed_action
from app.provider_actions.runtime import PRODUCT_KIND
from app.schema import CURRENT_SCHEMA_REVISION


def _postgres_url():
    value = os.environ.get("PUW_TRACK_E_TEST_DSN")
    if not value:
        pytest.skip("isolated Track E PostgreSQL DSN is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1"}
    assert parsed.database == "puw_track_e_test" and not parsed.query
    return value


@pytest.fixture
def postgres_world():
    engine = create_engine(_postgres_url(), hide_parameters=True, pool_size=5)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
    with sessions.begin() as db:
        org = Organization(name="Track E PostgreSQL race fixture")
        db.add(org); db.flush()
        user = User(name="Track E manager", email=f"track-e-{uuid4().hex}@example.invalid")
        db.add(user); db.flush()
        project = Project(name="Track E project", organization_id=org.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        db.add(GoogleOAuthToken(project_id=project.id,
                                scopes="https://www.googleapis.com/auth/tasks https://www.googleapis.com/auth/calendar.events",
                                access_token=None, refresh_token=None))
        task = Task(project_id=project.id, assignee_user_id=user.id, created_by_user_id=user.id,
                    title="Track E race", description="Synthetic only", status="assigned",
                    due_date=date(2035, 1, 2), source_type="synthetic", source_file_id="track-e-race",
                    source_file_name="fixture", source_excerpt="synthetic fixture",
                    source_excerpt_hash="e" * 64, confidence=1, needs_review=False)
        db.add(task); db.flush()
        ids = (org.id, project.id, user.id, task.id)
    try:
        yield sessions, ids
    finally:
        engine.dispose()


def test_two_simultaneous_confirms_create_one_action_and_one_job(postgres_world):
    sessions, (org_id, _project_id, user_id, task_id) = postgres_world
    barrier = Barrier(2)

    def confirm():
        with sessions() as db:
            actor = db.get(User, user_id)
            barrier.wait(timeout=10)
            return queue_confirmed_action(db, action_kind="google.tasks.upsert",
                                          target_id=task_id, actor=actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm)
        second = pool.submit(confirm)
        outcomes = (first.result(timeout=25), second.result(timeout=25))

    assert outcomes[0]["job_id"] == outcomes[1]["job_id"]
    assert {item["already_queued"] for item in outcomes} == {False, True}
    with sessions() as db:
        actions = list(db.scalars(select(ProviderAction).where(
            ProviderAction.organization_id == org_id,
            ProviderAction.action_id == f"google-task-{task_id}",
        )))
        jobs = list(db.scalars(select(BackgroundJob).where(
            BackgroundJob.kind == PRODUCT_KIND,
            BackgroundJob.idempotency_key == actions[0].idempotency_key,
        )))
        assert len(actions) == len(jobs) == 1
        assert db.get(ProviderDispatchOutbox, (actions[0].action_id, 1)).job_id == jobs[0].id
        assert set(jobs[0].payload) == {"organization_id", "action_id", "revision"}


def _claim(sessions, job_id: int, worker: str, attempt: int):
    now = datetime.now(timezone.utc)
    with sessions.begin() as db:
        job = db.get(BackgroundJob, job_id)
        job.status = "running"
        job.worker_id = worker
        job.attempts = attempt
        job.locked_at = now
        job.lease_expires_at = now + timedelta(minutes=5)
    return (job_id, worker, attempt, now)


def test_revoked_approval_cannot_dispatch_on_postgresql(postgres_world):
    sessions, (org_id, _project_id, user_id, task_id) = postgres_world
    with sessions() as db:
        queued = queue_confirmed_action(db, action_kind="google.tasks.upsert",
                                        target_id=task_id, actor=db.get(User, user_id))
    with sessions.begin() as db:
        approval = db.scalar(select(ProviderActionApproval).where(
            ProviderActionApproval.action_id == queued["action_id"],
        ))
        approval.state = "REVOKED"

    class NoProviderEffect:
        def __getattr__(self, _name):
            raise AssertionError("revoked action must not touch Google")

    runtime = build_product_runtime(sessions=sessions, service_factory=lambda *_: NoProviderEffect())
    with pytest.raises(ProviderActionError, match="dispatch_binding_mismatch"):
        runtime.execute_job(
            {"organization_id": org_id, "action_id": queued["action_id"], "revision": 1},
            _claim(sessions, queued["job_id"], "worker-revoked", 1),
        )


def test_crash_after_calendar_effect_uses_lookup_not_second_insert_on_postgresql(postgres_world):
    sessions, (org_id, _project_id, user_id, task_id) = postgres_world
    with sessions() as db:
        queued = queue_confirmed_action(db, action_kind="google.calendar.upsert",
                                        target_id=task_id, actor=db.get(User, user_id))

    class Request:
        def __init__(self, result, crash=False):
            self.result, self.crash = result, crash

        def execute(self):
            if self.crash:
                raise SystemExit("synthetic crash after external effect")
            return self.result

    class Calendar:
        def __init__(self):
            self.effects = 0
            self.crash = True
            self.items = {}

        def events(self):
            return self

        def insert(self, **kwargs):
            self.effects += 1
            item = {"id": kwargs["body"]["id"], **kwargs["body"]}
            self.items[item["id"]] = item
            return Request(item, self.crash)

        def get(self, **kwargs):
            return Request(self.items[kwargs["eventId"]])

    fake = Calendar()
    runtime = build_product_runtime(sessions=sessions, service_factory=lambda *_: fake)
    payload = {"organization_id": org_id, "action_id": queued["action_id"], "revision": 1}
    with pytest.raises(SystemExit):
        runtime.execute_job(payload, _claim(sessions, queued["job_id"], "worker-crashed", 1))
    fake.crash = False
    result = runtime.execute_job(payload, _claim(sessions, queued["job_id"], "worker-recovered", 2))
    assert result["outcome"] == "APPLIED" and fake.effects == 1
