from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register all tables
from app.api.mobile_sync import (
    ConflictResolutionRequest,
    MobileCommandRequest,
    resolve_mobile_sync_conflict,
    submit_mobile_command,
)
from app.api.tasks import TaskUpdate, update_task
from app.database import Base
from app.mobile_sync_tokens import issue_mobile_sync_token
from app.models.management import Notification
from app.models.mobile_sync import MobileSyncCommand, MobileSyncConflict
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task, TaskHistory
from app.models.user import User
from app.task_mutations import task_sync_snapshot


@pytest.fixture
def mobile_sync_world(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "mobile-sync-test-secret-that-is-long-enough")
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    organization = Organization(name="Offline test")
    owner = User(name="Owner", email="mobile-owner@example.test", is_admin=False)
    colleague = User(name="Colleague", email="mobile-colleague@example.test", is_admin=False)
    session.add_all([organization, owner, colleague])
    session.flush()
    project = Project(name="Offline project", organization_id=organization.id)
    session.add(project)
    session.flush()
    session.add_all([
        ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
        ProjectMember(project_id=project.id, user_id=colleague.id, role="editor"),
    ])
    task = Task(
        project_id=project.id,
        assignee_user_id=owner.id,
        created_by_user_id=owner.id,
        title="Inspect site",
        status="assigned",
        priority="normal",
        source_file_id="offline-source",
        source_file_name="source.txt",
        source_excerpt="Inspect the site",
        source_excerpt_hash="a" * 64,
        confidence=1.0,
        needs_review=False,
    )
    notice = Notification(
        project_id=project.id,
        user_id=owner.id,
        kind="deadline",
        title="Deadline",
        body="Soon",
        entity_type="task",
        entity_id=1,
        dedupe_key="offline-notice",
    )
    session.add_all([task, notice])
    session.commit()
    session.refresh(task)
    session.refresh(notice)

    try:
        yield session, organization, project, owner, colleague, task, notice
    finally:
        session.close()
        engine.dispose()


def _token(organization: Organization, project: Project, user: User, task: Task) -> str:
    return issue_mobile_sync_token({
        "v": 1,
        "organization_id": organization.id,
        "project_id": project.id,
        "user_id": user.id,
        "entity_type": "task",
        "entity_id": task.id,
        "record_version": task.record_version,
        "base": task_sync_snapshot(task),
    })


def _command(*, project_id: int, operation: str, entity_id: int, mutation_id: str,
             patch: dict | None = None, base_token: str | None = None,
             created_at: datetime | None = None) -> MobileCommandRequest:
    return MobileCommandRequest(**{
        "client_mutation_id": mutation_id,
        "device_id": "android-device-0001",
        "project_id": project_id,
        "operation": operation,
        "entity_id": entity_id,
        "base_token": base_token,
        "patch": patch or {},
        "client_created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
    })


def test_offline_task_command_is_durable_and_duplicate_returns_original_receipt(mobile_sync_world):
    db, organization, project, owner, _, task, _ = mobile_sync_world
    payload = _command(
        project_id=project.id,
        operation="task.update",
        entity_id=task.id,
        mutation_id="mutation-idempotent-0001",
        base_token=_token(organization, project, owner, task),
        patch={"status": "in_progress"},
    )

    first = submit_mobile_command(payload, db, owner)
    second = submit_mobile_command(payload, db, owner)

    assert first["status"] == "applied"
    assert first["receipt_id"] == second["receipt_id"]
    assert db.scalar(select(Task).where(Task.id == task.id)).status == "in_progress"
    assert db.query(MobileSyncCommand).count() == 1
    assert db.query(TaskHistory).count() == 1


def test_disjoint_server_and_offline_changes_are_automatically_rebased(mobile_sync_world):
    db, organization, project, owner, colleague, task, _ = mobile_sync_world
    token = _token(organization, project, owner, task)
    update_task(task.id, TaskUpdate(expected_record_version=task.record_version, status="in_progress"), db, owner)

    response = submit_mobile_command(_command(
        project_id=project.id,
        operation="task.update",
        entity_id=task.id,
        mutation_id="mutation-rebase-0001",
        base_token=token,
        patch={"assignee_user_id": colleague.id},
    ), db, owner)

    assert response["status"] == "applied"
    assert response["result"]["outcome"] == "rebased"
    db.refresh(task)
    assert task.status == "in_progress"
    assert task.assignee_user_id == colleague.id
    assert task.record_version == 3


def test_same_field_divergence_preserves_both_versions_for_explicit_resolution(mobile_sync_world):
    db, organization, project, owner, _, task, _ = mobile_sync_world
    token = _token(organization, project, owner, task)
    update_task(task.id, TaskUpdate(expected_record_version=task.record_version, status="in_progress"), db, owner)

    response = submit_mobile_command(_command(
        project_id=project.id,
        operation="task.update",
        entity_id=task.id,
        mutation_id="mutation-conflict-0001",
        base_token=token,
        patch={"status": "cancelled"},
    ), db, owner)

    assert response["status"] == "conflict"
    conflict = response["conflict"]
    assert conflict["base_values"]["status"] == "assigned"
    assert conflict["local_values"]["status"] == "cancelled"
    assert conflict["server_values"]["status"] == "in_progress"
    assert conflict["conflicting_fields"] == ["status"]
    db.refresh(task)
    assert task.status == "in_progress"


def test_latest_write_wins_is_explicit_and_notifies_about_overwrite(mobile_sync_world):
    db, organization, project, owner, _, task, _ = mobile_sync_world
    token = _token(organization, project, owner, task)
    update_task(task.id, TaskUpdate(expected_record_version=task.record_version, status="in_progress"), db, owner)
    response = submit_mobile_command(_command(
        project_id=project.id,
        operation="task.update",
        entity_id=task.id,
        mutation_id="mutation-lww-0001",
        base_token=token,
        patch={"status": "cancelled"},
        created_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ), db, owner)
    conflict_id = response["conflict"]["id"]

    resolved = resolve_mobile_sync_conflict(
        conflict_id,
        ConflictResolutionRequest(resolution="latest_write_wins"),
        db,
        owner,
    )

    assert resolved["status"] == "resolved"
    assert resolved["resolved_values"]["status"] == "cancelled"
    db.refresh(task)
    assert task.status == "cancelled"
    notification = db.scalar(select(Notification).where(
        Notification.dedupe_key == f"mobile-sync-conflict:{conflict_id}:resolved",
    ))
    assert notification is not None
    assert "перезаписано" in notification.body


def test_terminal_server_change_is_not_silently_rebased_over_offline_edit(mobile_sync_world):
    db, organization, project, owner, colleague, task, _ = mobile_sync_world
    token = _token(organization, project, owner, task)
    update_task(
        task.id,
        TaskUpdate(
            expected_record_version=task.record_version,
            status="completed",
            result_note="Server completion evidence",
        ),
        db,
        owner,
    )

    response = submit_mobile_command(_command(
        project_id=project.id,
        operation="task.update",
        entity_id=task.id,
        mutation_id="mutation-terminal-0001",
        base_token=token,
        patch={"assignee_user_id": colleague.id},
    ), db, owner)

    assert response["status"] == "conflict"
    assert "status" in response["conflict"]["conflicting_fields"]
    assert response["conflict"]["local_values"]["status"] == "assigned"
    db.refresh(task)
    assert task.status == "completed"


def test_offline_notification_read_is_monotonic_and_idempotent(mobile_sync_world):
    db, _, project, owner, _, _, notice = mobile_sync_world
    payload = _command(
        project_id=project.id,
        operation="notification.mark_read",
        entity_id=notice.id,
        mutation_id="notification-read-0001",
    )

    first = submit_mobile_command(payload, db, owner)
    second = submit_mobile_command(payload, db, owner)

    assert first["status"] == "applied"
    assert first["result"]["outcome"] == "applied"
    assert second["receipt_id"] == first["receipt_id"]
    db.refresh(notice)
    assert notice.is_read is True
    assert notice.record_version == 2


def test_tampered_base_token_fails_closed_without_mutating_task(mobile_sync_world):
    db, organization, project, owner, _, task, _ = mobile_sync_world
    token = _token(organization, project, owner, task)
    response = submit_mobile_command(_command(
        project_id=project.id,
        operation="task.update",
        entity_id=task.id,
        mutation_id="mutation-tampered-0001",
        base_token=f"{token[:-1]}X",
        patch={"status": "cancelled"},
    ), db, owner)

    assert response["status"] == "rejected"
    assert response["error_code"] == "invalid_mobile_sync_token"
    db.refresh(task)
    assert task.status == "assigned"
    assert db.query(MobileSyncConflict).count() == 0
