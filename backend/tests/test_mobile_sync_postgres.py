"""Real PostgreSQL races for durable Android/PWA offline synchronization."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register mapped tables
from app.api.mobile_sync import (
    ConflictResolutionRequest,
    MobileCommandRequest,
    resolve_mobile_sync_conflict,
    submit_mobile_command,
)
from app.mobile_sync_tokens import issue_mobile_sync_token
from app.models.mobile_sync import MobileSyncCommand, MobileSyncConflict
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task, TaskHistory
from app.models.user import User
from app.schema import CURRENT_SCHEMA_REVISION
from app.task_mutations import task_sync_snapshot


def _postgres_url() -> str:
    value = os.environ.get("PUW_MOBILE_SYNC_TEST_DSN") or os.environ.get("PUW_TRACK_E_TEST_DSN")
    if not value:
        pytest.skip("isolated mobile-sync PostgreSQL DSN is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1"}
    assert parsed.database in {"puw_mobile_sync_test", "puw_track_e_test"} and not parsed.query
    return value


@pytest.fixture
def postgres_mobile_world(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "postgres-mobile-sync-secret-that-is-long-enough")
    engine = create_engine(
        _postgres_url(), hide_parameters=True, pool_size=8,
        connect_args={"options": "-c statement_timeout=15000 -c lock_timeout=10000"},
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
    marker = uuid4().hex
    with sessions.begin() as db:
        organization = Organization(name=f"Mobile sync {marker}")
        db.add(organization); db.flush()
        owner = User(name="Mobile owner", email=f"mobile-owner-{marker}@example.invalid")
        colleague = User(name="Mobile colleague", email=f"mobile-colleague-{marker}@example.invalid")
        db.add_all([owner, colleague]); db.flush()
        project = Project(name=f"Mobile project {marker}", organization_id=organization.id)
        db.add(project); db.flush()
        db.add_all([
            ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
            ProjectMember(project_id=project.id, user_id=colleague.id, role="editor"),
        ])
        task = Task(
            project_id=project.id, assignee_user_id=owner.id, created_by_user_id=owner.id,
            title="PostgreSQL mobile race", status="assigned", priority="normal",
            source_type="synthetic", source_file_id=f"mobile-{marker}", source_file_name="fixture",
            source_excerpt="synthetic", source_excerpt_hash=marker.ljust(64, "0"),
            confidence=1, needs_review=False,
        )
        db.add(task); db.flush()
        ids = organization.id, project.id, owner.id, colleague.id, task.id
        token = issue_mobile_sync_token({
            "v": 1,
            "organization_id": organization.id,
            "project_id": project.id,
            "user_id": owner.id,
            "entity_type": "task",
            "entity_id": task.id,
            "record_version": task.record_version,
            "base": task_sync_snapshot(task),
        })
    try:
        yield sessions, ids, token
    finally:
        engine.dispose()


def _request(project_id: int, task_id: int, mutation_id: str, token: str, status: str) -> MobileCommandRequest:
    return MobileCommandRequest(
        client_mutation_id=mutation_id,
        device_id="android-postgres-device",
        project_id=project_id,
        operation="task.update",
        entity_id=task_id,
        base_token=token,
        patch={"status": status},
        client_created_at=datetime.now(timezone.utc),
    )


def test_same_mutation_race_creates_one_receipt_and_one_effect(postgres_mobile_world):
    sessions, (org_id, project_id, owner_id, _, task_id), token = postgres_mobile_world
    barrier = Barrier(2)
    mutation_id = f"same-mutation-{uuid4().hex}"
    request = _request(project_id, task_id, mutation_id, token, "in_progress")

    def submit():
        with sessions() as db:
            barrier.wait(timeout=10)
            return submit_mobile_command(
                request,
                db,
                db.get(User, owner_id),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(submit), pool.submit(submit)
        outcomes = first.result(timeout=25), second.result(timeout=25)

    assert outcomes[0]["receipt_id"] == outcomes[1]["receipt_id"]
    assert outcomes[0]["status"] == outcomes[1]["status"] == "applied"
    with sessions() as db:
        assert db.scalar(select(Task).where(Task.id == task_id)).record_version == 2
        assert len(list(db.scalars(select(MobileSyncCommand).where(
            MobileSyncCommand.organization_id == org_id,
            MobileSyncCommand.client_mutation_id == mutation_id,
        )))) == 1
        assert len(list(db.scalars(select(TaskHistory).where(TaskHistory.task_id == task_id)))) == 1


def test_two_divergent_offline_mutations_preserve_one_conflict(postgres_mobile_world):
    sessions, (_, project_id, owner_id, _, task_id), token = postgres_mobile_world
    barrier = Barrier(2)

    def submit(status: str):
        with sessions() as db:
            barrier.wait(timeout=10)
            return submit_mobile_command(
                _request(project_id, task_id, f"divergent-{status}-{uuid4().hex}", token, status),
                db,
                db.get(User, owner_id),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, "in_progress")
        second = pool.submit(submit, "cancelled")
        outcomes = first.result(timeout=25), second.result(timeout=25)

    assert {item["status"] for item in outcomes} == {"applied", "conflict"}
    with sessions() as db:
        conflict = db.scalar(select(MobileSyncConflict).where(
            MobileSyncConflict.project_id == project_id,
            MobileSyncConflict.entity_id == task_id,
        ))
        assert conflict is not None
        assert conflict.conflicting_fields == ["status"]
        assert {conflict.local_values["status"], conflict.server_values["status"]} == {
            "in_progress", "cancelled",
        }


def test_response_loss_replay_returns_committed_receipt_without_second_mutation(postgres_mobile_world):
    sessions, (_, project_id, owner_id, _, task_id), token = postgres_mobile_world
    mutation_id = f"crash-replay-{uuid4().hex}"
    request = _request(project_id, task_id, mutation_id, token, "in_progress")
    with sessions() as db:
        original = submit_mobile_command(request, db, db.get(User, owner_id))
    # Simulate a client crash/connection loss after server commit but before receipt persistence.
    with sessions() as db:
        recovered = submit_mobile_command(request, db, db.get(User, owner_id))

    assert recovered["receipt_id"] == original["receipt_id"]
    assert recovered["result"] == original["result"]
    with sessions() as db:
        assert db.get(Task, task_id).record_version == 2
        assert len(list(db.scalars(select(TaskHistory).where(TaskHistory.task_id == task_id)))) == 1


def test_concurrent_conflict_resolution_has_one_final_winner(postgres_mobile_world):
    sessions, (_, project_id, owner_id, _, task_id), token = postgres_mobile_world
    with sessions() as db:
        task = db.get(Task, task_id)
        task.status = "in_progress"
        task.record_version = 2
        db.commit()
        outcome = submit_mobile_command(
            _request(project_id, task_id, f"resolve-race-{uuid4().hex}", token, "cancelled"),
            db,
            db.get(User, owner_id),
        )
        conflict_id = outcome["conflict"]["id"]
    barrier = Barrier(2)

    def resolve(choice: str):
        with sessions() as db:
            barrier.wait(timeout=10)
            return resolve_mobile_sync_conflict(
                conflict_id,
                ConflictResolutionRequest(resolution=choice),
                db,
                db.get(User, owner_id),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(resolve, "keep_server")
        second = pool.submit(resolve, "apply_local")
        outcomes = first.result(timeout=25), second.result(timeout=25)

    assert outcomes[0]["status"] == outcomes[1]["status"] == "resolved"
    assert outcomes[0]["resolution"] == outcomes[1]["resolution"]
    with sessions() as db:
        conflict = db.get(MobileSyncConflict, conflict_id)
        assert conflict.status == "resolved"
        assert db.get(Task, task_id).record_version in {2, 3}
