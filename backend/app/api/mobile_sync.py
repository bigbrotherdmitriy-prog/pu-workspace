"""Typed, fail-closed offline synchronization for the Android/PWA client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.management import append_management_history
from app.api.tasks import TaskUpdate
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.mobile_sync_tokens import MobileSyncTokenError, verify_mobile_sync_token
from app.models.management import Notification
from app.models.mobile_sync import MobileSyncCommand, MobileSyncConflict
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.task_mutations import TASK_TERMINAL_STATES, apply_task_patch, task_sync_snapshot


router = APIRouter(prefix="/mobile-sync", tags=["mobile-sync"])


class MobileCommandRequest(BaseModel):
    client_mutation_id: str = Field(min_length=8, max_length=90, pattern=r"^[A-Za-z0-9_.:-]+$")
    device_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    project_id: int = Field(gt=0)
    operation: Literal["task.update", "notification.mark_read"]
    entity_id: int = Field(gt=0)
    base_token: str | None = Field(default=None, max_length=8000)
    patch: dict = Field(default_factory=dict)
    client_created_at: datetime


class ConflictResolutionRequest(BaseModel):
    resolution: Literal["keep_server", "apply_local", "latest_write_wins"]


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def _conflict_payload(row: MobileSyncConflict) -> dict:
    return {
        "id": row.id,
        "command_id": row.command_id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "base_values": row.base_values,
        "local_values": row.local_values,
        "server_values": row.server_values,
        "conflicting_fields": row.conflicting_fields,
        "server_record_version": row.server_record_version,
        "server_updated_at": row.server_updated_at,
        "status": row.status,
        "resolution": row.resolution,
        "resolved_values": row.resolved_values,
        "created_at": row.created_at,
        "resolved_at": row.resolved_at,
    }


def _command_payload(row: MobileSyncCommand, db: Session) -> dict:
    conflict = db.scalar(select(MobileSyncConflict).where(MobileSyncConflict.command_id == row.id))
    return {
        "receipt_id": row.id,
        "client_mutation_id": row.client_mutation_id,
        "operation": row.operation,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "status": row.status,
        "result": row.result,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "conflict": _conflict_payload(conflict) if conflict else None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _same_command(row: MobileSyncCommand, payload: dict) -> bool:
    return row.payload == payload


def _get_or_create_command(
    db: Session,
    *,
    request: MobileCommandRequest,
    user: User,
    organization_id: int,
    stored_payload: dict,
) -> tuple[MobileSyncCommand, bool]:
    existing = db.scalar(select(MobileSyncCommand).where(
        MobileSyncCommand.organization_id == organization_id,
        MobileSyncCommand.user_id == user.id,
        MobileSyncCommand.client_mutation_id == request.client_mutation_id,
    ).with_for_update())
    if existing is not None:
        if not _same_command(existing, stored_payload):
            raise HTTPException(409, {"code": "client_mutation_id_reused"})
        return existing, False

    command = MobileSyncCommand(
        organization_id=organization_id,
        project_id=request.project_id,
        user_id=user.id,
        client_mutation_id=request.client_mutation_id,
        device_id=request.device_id,
        operation=request.operation,
        entity_type=request.operation.split(".", 1)[0],
        entity_id=request.entity_id,
        base_record_version=None,
        client_created_at=request.client_created_at,
        payload=stored_payload,
        status="processing",
    )
    try:
        with db.begin_nested():
            db.add(command)
            db.flush()
        return command, True
    except IntegrityError:
        existing = db.scalar(select(MobileSyncCommand).where(
            MobileSyncCommand.organization_id == organization_id,
            MobileSyncCommand.user_id == user.id,
            MobileSyncCommand.client_mutation_id == request.client_mutation_id,
        ).with_for_update())
        if existing is None:
            raise
        if not _same_command(existing, stored_payload):
            raise HTTPException(409, {"code": "client_mutation_id_reused"})
        return existing, False


def _verified_task_base(request: MobileCommandRequest, user: User, organization_id: int) -> dict:
    if not request.base_token:
        raise HTTPException(422, {"code": "sync_base_token_required"})
    try:
        base = verify_mobile_sync_token(request.base_token)
    except MobileSyncTokenError as exc:
        raise HTTPException(409, {"code": str(exc)}) from exc
    expected = {
        "v": 1,
        "organization_id": organization_id,
        "project_id": request.project_id,
        "user_id": user.id,
        "entity_type": "task",
        "entity_id": request.entity_id,
    }
    if any(base.get(key) != value for key, value in expected.items()):
        raise HTTPException(409, {"code": "sync_base_binding_mismatch"})
    if not isinstance(base.get("record_version"), int) or not isinstance(base.get("base"), dict):
        raise HTTPException(409, {"code": "invalid_mobile_sync_token"})
    return base


def _normalize_task_patch(request: MobileCommandRequest) -> tuple[dict, set[str], dict]:
    if not request.patch:
        raise HTTPException(422, {"code": "empty_patch"})
    if "expected_record_version" in request.patch:
        raise HTTPException(422, {"code": "expected_record_version_not_allowed"})
    try:
        parsed = TaskUpdate(expected_record_version=1, **request.patch)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_task_patch", "message": str(exc)}) from exc
    fields_set = parsed.model_fields_set - {"expected_record_version"}
    if not fields_set:
        raise HTTPException(422, {"code": "empty_patch"})
    python_values = parsed.model_dump(exclude={"expected_record_version"}, exclude_unset=True)
    json_values = parsed.model_dump(mode="json", exclude={"expected_record_version"}, exclude_unset=True)
    return python_values, fields_set, json_values


def _task_conflicting_fields(base: dict, current: dict, desired: dict, fields_set: set[str]) -> list[str]:
    business_fields = fields_set - {"due_change_reason"}
    conflicts = [
        field for field in sorted(business_fields)
        if current.get(field) != base.get(field) and current.get(field) != desired.get(field)
    ]
    if (
        base.get("status") != current.get("status")
        and current.get("status") in TASK_TERMINAL_STATES
        and any(field != "status" for field in business_fields)
        and "status" not in conflicts
    ):
        conflicts.append("status")
    return conflicts


def _create_task_conflict(
    db: Session,
    *,
    command: MobileSyncCommand,
    base: dict,
    desired: dict,
    current: dict,
    conflicting_fields: list[str],
    task: Task,
) -> MobileSyncConflict:
    local_values = dict(desired)
    # A terminal server transition conflicts with an offline edit even when
    # status was not explicitly edited. Preserve the local/base status so an
    # explicit "apply local" really selects the complete local alternative.
    for field in conflicting_fields:
        if field not in local_values and field in base:
            local_values[field] = base[field]
    row = MobileSyncConflict(
        command_id=command.id,
        organization_id=command.organization_id,
        project_id=command.project_id,
        user_id=command.user_id,
        entity_type="task",
        entity_id=task.id,
        base_values=base,
        local_values=local_values,
        server_values=current,
        conflicting_fields=conflicting_fields,
        server_record_version=task.record_version,
        server_updated_at=task.updated_at,
        status="unresolved",
    )
    db.add(row)
    db.flush()
    command.status = "conflict"
    command.result = {"conflict_id": row.id, "record_version": task.record_version}
    return row


def _apply_task_command(
    db: Session, *, command: MobileSyncCommand, request: MobileCommandRequest, user: User,
) -> None:
    base_payload = _verified_task_base(request, user, command.organization_id)
    command.base_record_version = base_payload["record_version"]
    base = base_payload["base"]
    python_patch, fields_set, desired = _normalize_task_patch(request)
    task = db.scalar(select(Task).where(Task.id == request.entity_id).with_for_update())
    if task is None or task.project_id != request.project_id:
        raise HTTPException(404, "Task not found")
    require_project_role(db, user, task.project_id, "editor")
    current = task_sync_snapshot(task)
    business_fields = fields_set - {"due_change_reason"}

    if all(current.get(field) == desired.get(field) for field in business_fields):
        command.status = "applied"
        command.result = {
            "outcome": "already_applied",
            "record_version": task.record_version,
            "entity": current,
        }
        return

    conflicts = []
    if task.record_version != base_payload["record_version"]:
        conflicts = _task_conflicting_fields(base, current, desired, fields_set)
    if conflicts:
        _create_task_conflict(
            db,
            command=command,
            base=base,
            desired=desired,
            current=current,
            conflicting_fields=conflicts,
            task=task,
        )
        return

    apply_task_patch(
        db,
        task=task,
        actor=user,
        changes=python_patch,
        fields_set=fields_set,
        history_idempotency_key=f"mobile:{command.client_mutation_id}",
    )
    db.flush()
    command.status = "applied"
    command.result = {
        "outcome": "rebased" if base_payload["record_version"] != task.record_version - 1 else "applied",
        "record_version": task.record_version,
        "entity": task_sync_snapshot(task),
    }


def _apply_notification_command(
    db: Session, *, command: MobileSyncCommand, request: MobileCommandRequest, user: User,
) -> None:
    if request.patch:
        raise HTTPException(422, {"code": "notification_patch_not_allowed"})
    item = db.scalar(select(Notification).where(
        Notification.id == request.entity_id,
        Notification.user_id == user.id,
    ).with_for_update())
    if item is None or item.project_id != request.project_id:
        raise HTTPException(404, "Notification not found")
    require_project_role(db, user, item.project_id, "viewer")
    already_read = item.is_read
    if not already_read:
        item.is_read = True
        item.record_version += 1
        append_management_history(
            db,
            project_id=item.project_id,
            entity_type="notification",
            entity_id=item.id,
            record_version=item.record_version,
            action="read",
            actor_user_id=user.id,
            old_values={"is_read": False},
            new_values={"is_read": True},
            evidence={"kind": item.kind, "entity_type": item.entity_type, "entity_id": item.entity_id},
            reason="Офлайн-отметка синхронизирована",
            idempotency_key=f"mobile:{command.client_mutation_id}",
        )
    command.status = "applied"
    command.result = {
        "outcome": "already_applied" if already_read else "applied",
        "record_version": item.record_version,
        "entity": {"is_read": True},
    }


@router.post("/commands")
def submit_mobile_command(
    request: MobileCommandRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    project = _project(db, request.project_id)
    minimum_role = "editor" if request.operation == "task.update" else "viewer"
    require_project_role(db, user, project.id, minimum_role)
    stored_payload = request.model_dump(mode="json")
    command, created = _get_or_create_command(
        db,
        request=request,
        user=user,
        organization_id=project.organization_id,
        stored_payload=stored_payload,
    )
    if not created:
        return _command_payload(command, db)

    try:
        with db.begin_nested():
            if request.operation == "task.update":
                _apply_task_command(db, command=command, request=request, user=user)
            else:
                _apply_notification_command(db, command=command, request=request, user=user)
    except HTTPException as exc:
        command.status = "rejected"
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        command.error_code = str(detail.get("code") or f"http_{exc.status_code}")
        command.error_message = str(detail.get("message") or detail.get("code") or exc.detail)
    db.commit()
    db.refresh(command)
    return _command_payload(command, db)


@router.get("/status")
def mobile_sync_status(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    project = _project(db, project_id)
    require_project_role(db, user, project.id, "viewer")
    conflicts = list(db.scalars(select(MobileSyncConflict).where(
        MobileSyncConflict.project_id == project_id,
        MobileSyncConflict.user_id == user.id,
        MobileSyncConflict.status == "unresolved",
    ).order_by(MobileSyncConflict.id.asc())))
    return {"conflicts": [_conflict_payload(row) for row in conflicts], "count": len(conflicts)}


def _notify_conflict_resolution(
    db: Session,
    *,
    conflict: MobileSyncConflict,
    user_id: int,
    local_won: bool,
) -> None:
    title = "Конфликт офлайн-синхронизации разрешён"
    body = (
        "Локальная версия применена; более раннее серверное изменение было перезаписано."
        if local_won
        else "Сохранена серверная версия; локальное офлайн-изменение не применено."
    )
    existing = db.scalar(select(Notification).where(
        Notification.user_id == user_id,
        Notification.dedupe_key == f"mobile-sync-conflict:{conflict.id}:resolved",
    ))
    if existing is None:
        db.add(Notification(
            project_id=conflict.project_id,
            user_id=user_id,
            kind="mobile_sync_conflict",
            title=title,
            body=body,
            entity_type=conflict.entity_type,
            entity_id=conflict.entity_id,
            dedupe_key=f"mobile-sync-conflict:{conflict.id}:resolved",
        ))


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_mobile_sync_conflict(
    conflict_id: int,
    request: ConflictResolutionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    conflict = db.scalar(select(MobileSyncConflict).where(
        MobileSyncConflict.id == conflict_id,
        MobileSyncConflict.user_id == user.id,
    ).with_for_update())
    if conflict is None:
        raise HTTPException(404, "Conflict not found")
    require_project_role(db, user, conflict.project_id, "editor")
    if conflict.status == "resolved":
        return _conflict_payload(conflict)
    command = db.get(MobileSyncCommand, conflict.command_id)
    task = db.scalar(select(Task).where(Task.id == conflict.entity_id).with_for_update())
    if task is None or task.project_id != conflict.project_id:
        raise HTTPException(404, "Task not found")
    if task.record_version != conflict.server_record_version:
        raise HTTPException(409, {
            "code": "conflict_changed",
            "expected": conflict.server_record_version,
            "actual": task.record_version,
        })

    local_won = request.resolution == "apply_local"
    if request.resolution == "latest_write_wins":
        local_won = _utc(command.client_created_at) >= _utc(conflict.server_updated_at)

    if local_won:
        parsed = TaskUpdate(expected_record_version=task.record_version, **conflict.local_values)
        apply_task_patch(
            db,
            task=task,
            actor=user,
            changes=parsed.model_dump(exclude={"expected_record_version"}, exclude_unset=True),
            fields_set=parsed.model_fields_set - {"expected_record_version"},
            history_idempotency_key=f"resolve:{conflict.id}",
        )
        db.flush()
        resolved_values = task_sync_snapshot(task)
    else:
        resolved_values = task_sync_snapshot(task)

    conflict.status = "resolved"
    conflict.resolution = request.resolution
    conflict.resolved_values = resolved_values
    conflict.resolved_by_user_id = user.id
    conflict.resolved_at = datetime.now(timezone.utc)
    command.status = "applied"
    command.result = {
        "outcome": "resolved_local" if local_won else "resolved_server",
        "record_version": task.record_version,
        "entity": resolved_values,
    }
    _notify_conflict_resolution(db, conflict=conflict, user_id=user.id, local_won=local_won)
    db.commit()
    db.refresh(conflict)
    return _conflict_payload(conflict)
