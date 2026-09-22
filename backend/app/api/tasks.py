from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.task import Task, TaskDueDateHistory, TaskHistory
from app.models.document import Document
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.management import Obligation
from app.models.project import Project
from app.integrations.external_resources import external_id_for
from app.integrations.actions import configured_action_adapter
from app.provider_actions.contracts import ProviderActionError
from app.provider_actions.product import queue_confirmed_action, task_effect_states
from app.api.management import _locked_versioned, append_management_history
from app.task_mutations import apply_task_patch
from app.task_mutations import task_sync_snapshot
from app.mobile_sync_tokens import maybe_issue_mobile_sync_token

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskUpdate(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    status: str | None = Field(default=None, pattern="^(assigned|in_progress|completed|cancelled)$")
    due_date: date | None = None
    due_change_reason: str | None = Field(default=None, max_length=2000)
    result_note: str | None = Field(default=None, max_length=5000)
    completion_document_id: int | None = Field(default=None, ge=1)
    assignee_user_id: int | None = Field(default=None, ge=1)


class ExternalActionApproval(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    publish_task: bool = Field(
        default=True,
        validation_alias=AliasChoices("publish_task", "create_google_task"),
    )
    publish_calendar: bool = Field(
        default=True,
        validation_alias=AliasChoices("publish_calendar", "create_calendar_event"),
    )


@router.get("")
def list_tasks(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
               status: str | None = None, cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be between 1 and 200")
    action_provider = configured_action_adapter(project_id, db).provider
    original_obligation_id = (
        select(Obligation.id)
        .where(Obligation.task_id == Task.id)
        .order_by(Obligation.id.asc())
        .limit(1)
        .scalar_subquery()
    )
    original_obligation_due_date = (
        select(Obligation.due_date)
        .where(Obligation.task_id == Task.id)
        .order_by(Obligation.id.asc())
        .limit(1)
        .scalar_subquery()
    )
    query = (
        select(Task, User, original_obligation_id, original_obligation_due_date)
        .join(User, User.id == Task.assignee_user_id)
        .where(Task.project_id == project_id)
    )
    if status: query = query.where(Task.status == status)
    if cursor is not None: query = query.where(Task.id < cursor)
    rows = db.execute(query.order_by(Task.id.desc()).limit(limit + 1)).all()
    has_more = len(rows) > limit; rows = rows[:limit]
    result = []
    for task, assignee, obligation_id, obligation_due_date in rows:
        external_task_id = external_id_for(
            db, entity_type="task", entity_id=task.id, provider=action_provider,
            resource_type="task", legacy_id=task.google_task_id,
        )
        external_calendar_id = external_id_for(
            db, entity_type="task", entity_id=task.id, provider=action_provider,
            resource_type="calendar_event", legacy_id=task.google_calendar_event_id,
        )
        result.append({
            "id": task.id, "record_version": task.record_version, "title": task.title, "status": task.status, "priority": task.priority,
            "description": task.description,
            "due_date": task.due_date,
            "original_obligation_id": obligation_id,
            "original_obligation_due_date": obligation_due_date,
            "due_date_adjusted": obligation_id is not None and task.due_date != obligation_due_date,
            "assignee_user_id": task.assignee_user_id,
            "assignee_name": assignee.name, "assignee_email": assignee.email,
            "source_file_name": task.source_file_name, "source_excerpt": task.source_excerpt,
            "confidence": task.confidence, "needs_review": task.needs_review,
            "message_id": task.message_id, "external_action_status": task.external_action_status,
            "provider_effects": task_effect_states(db, task.id),
            "google_task_id": external_task_id, "google_sync_error": task.google_sync_error,
            "google_calendar_event_id": external_calendar_id,
            "google_calendar_sync_error": task.google_calendar_sync_error,
            "external_resources": [
                *([{"provider": action_provider, "resource_type": "task", "external_id": external_task_id}] if external_task_id else []),
                *([{"provider": action_provider, "resource_type": "calendar_event", "external_id": external_calendar_id}] if external_calendar_id else []),
            ],
            "result_note": task.result_note, "completed_at": task.completed_at,
            "completion_document_id": task.completion_document_id,
            "completion_document_name": db.get(Document, task.completion_document_id).name if task.completion_document_id and db.get(Document, task.completion_document_id) else None,
            "sync_base_token": maybe_issue_mobile_sync_token({
                "v": 1,
                "organization_id": db.get(Project, task.project_id).organization_id,
                "project_id": task.project_id,
                "user_id": user.id,
                "entity_type": "task",
                "entity_id": task.id,
                "record_version": task.record_version,
                "base": task_sync_snapshot(task),
            }),
        })
    return {"tasks": result, "count": len(rows),
            "next_cursor": rows[-1][0].id if has_more and rows else None}


@router.patch("/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = _locked_versioned(db, Task, task_id, payload.expected_record_version, "Task")
    require_project_role(db, user, task.project_id, "editor")
    apply_task_patch(
        db,
        task=task,
        actor=user,
        changes=payload.model_dump(exclude={"expected_record_version"}, exclude_unset=True),
        fields_set=payload.model_fields_set - {"expected_record_version"},
    )
    db.commit(); db.refresh(task)
    return {"id": task.id, "record_version": task.record_version, "status": task.status, "due_date": task.due_date, "result_note": task.result_note,
            "completion_document_id": task.completion_document_id, "completed_at": task.completed_at,
            "assignee_user_id": task.assignee_user_id}


@router.get("/{task_id}/history")
def task_history(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_project_role(db, user, task.project_id, "viewer")
    rows = db.execute(
        select(TaskHistory, User, Document)
        .join(User, User.id == TaskHistory.changed_by_user_id)
        .outerjoin(Document, Document.id == TaskHistory.completion_document_id)
        .where(TaskHistory.task_id == task_id)
        .order_by(TaskHistory.changed_at.asc(), TaskHistory.id.asc())
    ).all()
    history = [{"action": "created", "new_status": "assigned", "changed_at": task.created_at,
                "changed_by": "Система", "result_note": None, "completion_document_id": None,
                "completion_document_name": None, "details": "Задача создана из подтверждённого источника"}]
    history.extend({"action": row.action, "old_status": row.old_status, "new_status": row.new_status,
                    "result_note": row.result_note, "completion_document_id": row.completion_document_id,
                    "completion_document_name": document.name if document else None,
                    "details": row.details, "changed_by": changed_by.name, "changed_at": row.changed_at}
                   for row, changed_by, document in rows)
    return {"history": history}


@router.get("/{task_id}/due-history")
def due_history(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_project_role(db, user, task.project_id, "viewer")
    rows = db.scalars(select(TaskDueDateHistory).where(TaskDueDateHistory.task_id == task_id).order_by(TaskDueDateHistory.changed_at.desc())).all()
    return {"history": [{"old_due_date": x.old_due_date, "new_due_date": x.new_due_date, "reason": x.reason, "changed_at": x.changed_at} for x in rows]}


def _sync_actions(project_id: int, db: Session, user: User):
    require_project_role(db, user, project_id, "manager")
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id, Task.external_action_status == "approved")).all())
    queued = failed = 0
    for task in tasks:
        try:
            queue_confirmed_action(db, action_kind="google.tasks.upsert", target_id=task.id, actor=user)
            if task.due_date:
                queue_confirmed_action(db, action_kind="google.calendar.upsert", target_id=task.id, actor=user)
            task.external_action_status = "queued"
            db.commit()
            queued += 1
        except ProviderActionError:
            db.rollback()
            failed += 1
    return {"provider": "google_workspace", "queued": queued, "failed": failed,
            "synced": 0, "calendar_synced": 0, "calendar_failed": failed}


@router.post("/sync-actions")
def sync_actions(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _sync_actions(project_id, db, user)


@router.post("/sync-google")
def sync_google(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Backward-compatible route for the existing UI."""
    return _sync_actions(project_id, db, user)


@router.post("/{task_id}/approve-external")
def approve_external(task_id: int, payload: ExternalActionApproval, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = _locked_versioned(db, Task, task_id, payload.expected_record_version, "Task")
    require_project_role(db, user, task.project_id, "manager")
    if not payload.publish_task and not payload.publish_calendar:
        raise HTTPException(422, "Select at least one external action")
    if payload.publish_calendar and not task.due_date:
        raise HTTPException(422, "Calendar action requires a task due date")
    old_external_status = task.external_action_status
    if task.message_id is not None and task.needs_review:
        task.needs_review = False
    task.external_action_status = "approved"
    task.record_version += 1
    append_management_history(
        db, project_id=task.project_id, entity_type="task", entity_id=task.id,
        record_version=task.record_version, action="external_action_approved", actor_user_id=user.id,
        old_values={"external_action_status": old_external_status},
        new_values={"external_action_status": task.external_action_status},
        evidence={"source_file_id": task.source_file_id, "source_excerpt_hash": task.source_excerpt_hash},
        reason="Пользователь подтвердил внешнее действие",
    )
    adapter = configured_action_adapter(task.project_id, db)
    db.commit()
    queued_actions = []
    failures = []
    for selected, kind in ((payload.publish_task, "google.tasks.upsert"),
                           (payload.publish_calendar, "google.calendar.upsert")):
        if not selected:
            continue
        try:
            queued_actions.append(queue_confirmed_action(db, action_kind=kind, target_id=task.id, actor=user))
        except ProviderActionError as exc:
            db.rollback()
            failures.append({"action_kind": kind, "code": exc.code})
    task = db.get(Task, task.id)
    task_synced = calendar_synced = 0
    task_failed = int(any(item["action_kind"] == "google.tasks.upsert" for item in failures))
    calendar_failed = int(any(item["action_kind"] == "google.calendar.upsert" for item in failures))
    external_task_id = external_id_for(
        db, entity_type="task", entity_id=task.id, provider=adapter.provider,
        resource_type="task", legacy_id=task.google_task_id,
    )
    external_calendar_id = external_id_for(
        db, entity_type="task", entity_id=task.id, provider=adapter.provider,
        resource_type="calendar_event", legacy_id=task.google_calendar_event_id,
    )
    previous_external_status = task.external_action_status
    task.external_action_status = "queued" if queued_actions else "failed"
    # The outbox seals this exact record_version. The internal queue-status
    # projection must not invalidate its payload before the worker starts.
    append_management_history(
        db, project_id=task.project_id, entity_type="task", entity_id=task.id,
        record_version=task.record_version, action="external_action_finished", actor_user_id=user.id,
        old_values={"external_action_status": previous_external_status},
        new_values={"external_action_status": task.external_action_status},
        evidence={"provider": adapter.provider, "external_task_id": external_task_id,
                  "external_calendar_id": external_calendar_id},
        reason="Внешнее действие поставлено в очередь" if queued_actions else "Ошибка постановки в очередь",
    )
    db.add(AuditLog(action="external_task_action", entity_type="task", entity_id=task.id,
                    details=f"provider={adapter.provider}; queued={len(queued_actions)}; failures={len(failures)}"))
    db.commit(); db.refresh(task)
    return {"id": task.id, "record_version": task.record_version, "provider": adapter.provider,
            "external_action_status": task.external_action_status,
            "provider_effects": task_effect_states(db, task.id),
            "google_task_id": external_task_id, "google_calendar_event_id": external_calendar_id,
            "external_resources": [
                *([{"provider": adapter.provider, "resource_type": "task", "external_id": external_task_id}] if external_task_id else []),
                *([{"provider": adapter.provider, "resource_type": "calendar_event", "external_id": external_calendar_id}] if external_calendar_id else []),
            ],
            "task_failed": task_failed, "google_task_failed": task_failed,
            "calendar_failed": calendar_failed, "actions": queued_actions, "queue_failures": failures}
