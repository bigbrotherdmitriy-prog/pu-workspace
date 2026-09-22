"""Shared transactional task mutation logic for online and offline clients."""

from __future__ import annotations

from collections.abc import Mapping, Set

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.management import append_management_history
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.management import Obligation
from app.models.project_member import ProjectMember
from app.models.task import Task, TaskDueDateHistory, TaskHistory
from app.models.user import User


TASK_EDITABLE_FIELDS = frozenset({
    "status", "due_date", "due_change_reason", "result_note",
    "completion_document_id", "assignee_user_id",
})
TASK_SYNC_SNAPSHOT_FIELDS = frozenset({
    "status", "due_date", "result_note", "completion_document_id", "assignee_user_id",
})
TASK_TERMINAL_STATES = frozenset({"completed", "cancelled"})


def task_sync_snapshot(task: Task) -> dict:
    return {
        "status": task.status,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "result_note": task.result_note,
        "completion_document_id": task.completion_document_id,
        "assignee_user_id": task.assignee_user_id,
    }


def apply_task_patch(
    db: Session,
    *,
    task: Task,
    actor: User,
    changes: Mapping[str, object],
    fields_set: Set[str],
    history_idempotency_key: str | None = None,
) -> Task:
    """Apply a validated task patch without committing the surrounding transaction."""

    unexpected = set(fields_set) - TASK_EDITABLE_FIELDS
    if unexpected:
        raise HTTPException(422, f"Unsupported task fields: {', '.join(sorted(unexpected))}")

    old_snapshot = {
        "status": task.status,
        "due_date": task.due_date,
        "assignee_user_id": task.assignee_user_id,
        "result_note": task.result_note,
        "completion_document_id": task.completion_document_id,
    }
    old_status = task.status
    old_due_date = task.due_date
    old_assignee_user_id = task.assignee_user_id
    changed = False

    if "assignee_user_id" in fields_set:
        assignee_user_id = changes.get("assignee_user_id")
        if assignee_user_id is None:
            raise HTTPException(422, "Исполнитель обязателен")
        membership = db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == task.project_id,
            ProjectMember.user_id == assignee_user_id,
        ))
        if membership is None:
            raise HTTPException(422, "Исполнитель должен быть участником проекта")
        task.assignee_user_id = int(assignee_user_id)
        changed = changed or task.assignee_user_id != old_assignee_user_id

    if "due_date" in fields_set and changes.get("due_date") != task.due_date:
        due_change_reason = str(changes.get("due_change_reason") or "").strip()
        if not due_change_reason:
            raise HTTPException(422, "Причина переноса срока обязательна")
        db.add(TaskDueDateHistory(
            task_id=task.id,
            old_due_date=task.due_date,
            new_due_date=changes.get("due_date"),
            reason=due_change_reason,
            changed_by_user_id=actor.id,
        ))
        task.due_date = changes.get("due_date")
        task.google_calendar_sync_error = None
        changed = True

    requested_status = changes.get("status")
    if requested_status:
        if requested_status == "completed" and not (
            changes.get("result_note") or task.result_note or ""
        ).strip():
            raise HTTPException(422, "Для завершения задачи укажите подтверждаемый результат")
        task.status = str(requested_status)
        from datetime import datetime, timezone
        task.completed_at = datetime.now(timezone.utc) if requested_status == "completed" else None
        changed = changed or requested_status != old_status

    if "result_note" in fields_set and changes.get("result_note") is not None:
        value = changes.get("result_note")
        new_note = str(value).strip() if value is not None else None
        new_note = new_note or None
        changed = changed or new_note != task.result_note
        task.result_note = new_note

    if "completion_document_id" in fields_set:
        completion_document_id = changes.get("completion_document_id")
        document = db.get(Document, completion_document_id) if completion_document_id else None
        if completion_document_id and (not document or document.project_id != task.project_id):
            raise HTTPException(422, "Подтверждающий документ должен относиться к проекту задачи")
        changed = changed or completion_document_id != task.completion_document_id
        task.completion_document_id = completion_document_id

    if not changed:
        return task

    task.record_version += 1
    if task.external_action_status in {"approved", "queued", "executing", "executed", "unknown"}:
        task.external_action_status = "proposed"

    details = []
    if old_due_date != task.due_date:
        details.append(f"Срок: {old_due_date or 'не задан'} → {task.due_date or 'не задан'}")
    if old_assignee_user_id != task.assignee_user_id:
        assignee = db.get(User, task.assignee_user_id)
        details.append(f"Исполнитель: {assignee.name if assignee else task.assignee_user_id}")
    db.add(TaskHistory(
        task_id=task.id,
        action="completed" if task.status == "completed" and old_status != "completed" else "updated",
        old_status=old_status,
        new_status=task.status,
        result_note=task.result_note,
        completion_document_id=task.completion_document_id,
        details="; ".join(details) or None,
        changed_by_user_id=actor.id,
    ))
    db.add(AuditLog(
        action="task_updated", entity_type="task", entity_id=task.id,
        details=(
            f"user={actor.id}; status={old_status}->{task.status}; "
            f"completion_document_id={task.completion_document_id}"
        ),
    ))
    append_management_history(
        db,
        project_id=task.project_id,
        entity_type="task",
        entity_id=task.id,
        record_version=task.record_version,
        action="updated",
        actor_user_id=actor.id,
        old_values=old_snapshot,
        new_values={
            "status": task.status,
            "due_date": task.due_date,
            "assignee_user_id": task.assignee_user_id,
            "result_note": task.result_note,
            "completion_document_id": task.completion_document_id,
        },
        evidence={
            "source_file_id": task.source_file_id,
            "source_excerpt_hash": task.source_excerpt_hash,
            "completion_document_id": task.completion_document_id,
        },
        reason=str(changes.get("due_change_reason") or "") or task.result_note,
        idempotency_key=history_idempotency_key,
    )
    return task
