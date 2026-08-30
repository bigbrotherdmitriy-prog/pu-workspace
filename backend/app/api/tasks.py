from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.task import Task, TaskDueDateHistory, TaskHistory
from app.models.document import Document
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.audit_log import AuditLog
from app.integrations.external_resources import external_id_for
from app.integrations.actions import configured_action_adapter, publish_actions

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(assigned|in_progress|completed|cancelled)$")
    due_date: date | None = None
    due_change_reason: str | None = Field(default=None, max_length=2000)
    result_note: str | None = Field(default=None, max_length=5000)
    completion_document_id: int | None = Field(default=None, ge=1)
    assignee_user_id: int | None = Field(default=None, ge=1)


class ExternalActionApproval(BaseModel):
    publish_task: bool = Field(
        default=True,
        validation_alias=AliasChoices("publish_task", "create_google_task"),
    )
    publish_calendar: bool = Field(
        default=True,
        validation_alias=AliasChoices("publish_calendar", "create_calendar_event"),
    )


@router.get("")
def list_tasks(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    action_provider = configured_action_adapter(project_id, db).provider
    rows = db.execute(
        select(Task, User).join(User, User.id == Task.assignee_user_id)
        .where(Task.project_id == project_id).order_by(Task.created_at.desc(), Task.id.desc())
    ).all()
    result = []
    for task, assignee in rows:
        external_task_id = external_id_for(
            db, entity_type="task", entity_id=task.id, provider=action_provider,
            resource_type="task", legacy_id=task.google_task_id,
        )
        external_calendar_id = external_id_for(
            db, entity_type="task", entity_id=task.id, provider=action_provider,
            resource_type="calendar_event", legacy_id=task.google_calendar_event_id,
        )
        result.append({
            "id": task.id, "title": task.title, "status": task.status, "priority": task.priority,
            "due_date": task.due_date, "assignee_user_id": task.assignee_user_id,
            "assignee_name": assignee.name, "assignee_email": assignee.email,
            "source_file_name": task.source_file_name, "source_excerpt": task.source_excerpt,
            "confidence": task.confidence, "needs_review": task.needs_review,
            "message_id": task.message_id, "external_action_status": task.external_action_status,
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
        })
    return {"tasks": result, "count": len(rows)}


@router.patch("/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_project_role(db, user, task.project_id, "editor")
    old_status = task.status
    old_due_date = task.due_date
    old_assignee_user_id = task.assignee_user_id
    changed = False
    if "assignee_user_id" in payload.model_fields_set:
        membership = db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == task.project_id,
            ProjectMember.user_id == payload.assignee_user_id,
        ))
        if membership is None:
            raise HTTPException(422, "Исполнитель должен быть участником проекта")
        task.assignee_user_id = payload.assignee_user_id
        changed = changed or task.assignee_user_id != old_assignee_user_id
    if "due_date" in payload.model_fields_set and payload.due_date != task.due_date:
        if not (payload.due_change_reason or "").strip():
            raise HTTPException(422, "Причина переноса срока обязательна")
        db.add(TaskDueDateHistory(task_id=task.id, old_due_date=task.due_date, new_due_date=payload.due_date, reason=payload.due_change_reason.strip(), changed_by_user_id=user.id))
        task.due_date = payload.due_date
        task.google_calendar_event_id = None
        task.google_calendar_sync_error = None
        changed = True
    if payload.status:
        if payload.status == "completed" and not (payload.result_note or task.result_note or "").strip():
            raise HTTPException(422, "Для завершения задачи укажите подтверждаемый результат")
        task.status = payload.status
        task.completed_at = datetime.now(timezone.utc) if payload.status == "completed" else None
        changed = changed or payload.status != old_status
    if payload.result_note is not None:
        new_note = payload.result_note.strip() or None
        changed = changed or new_note != task.result_note
        task.result_note = new_note
    if "completion_document_id" in payload.model_fields_set:
        document = db.get(Document, payload.completion_document_id) if payload.completion_document_id else None
        if payload.completion_document_id and (not document or document.project_id != task.project_id):
            raise HTTPException(422, "Подтверждающий документ должен относиться к проекту задачи")
        changed = changed or payload.completion_document_id != task.completion_document_id
        task.completion_document_id = payload.completion_document_id
    if changed:
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
            changed_by_user_id=user.id,
        ))
        db.add(AuditLog(action="task_updated", entity_type="task", entity_id=task.id,
                        details=f"user={user.id}; status={old_status}->{task.status}; completion_document_id={task.completion_document_id}"))
    db.commit(); db.refresh(task)
    if task.external_action_status == "executed":
        publish_actions(configured_action_adapter(task.project_id, db), [task], force_update=True)
    return {"id": task.id, "status": task.status, "due_date": task.due_date, "result_note": task.result_note,
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
    adapter = configured_action_adapter(project_id, db)
    result = publish_actions(adapter, tasks)
    return {"provider": adapter.provider, "synced": result.task_synced, "failed": result.task_failed,
            "calendar_synced": result.calendar_synced, "calendar_failed": result.calendar_failed}


@router.post("/sync-actions")
def sync_actions(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _sync_actions(project_id, db, user)


@router.post("/sync-google")
def sync_google(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Backward-compatible route for the existing UI."""
    return _sync_actions(project_id, db, user)


@router.post("/{task_id}/approve-external")
def approve_external(task_id: int, payload: ExternalActionApproval, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    require_project_role(db, user, task.project_id, "manager")
    if not payload.publish_task and not payload.publish_calendar:
        raise HTTPException(422, "Select at least one external action")
    if task.message_id is not None and task.needs_review:
        task.needs_review = False
    task.external_action_status = "approved"
    db.commit()
    adapter = configured_action_adapter(task.project_id, db)
    result = publish_actions(
        adapter, [task],
        publish_tasks=payload.publish_task,
        publish_calendar=payload.publish_calendar and bool(task.due_date),
    )
    task_synced, task_failed = result.task_synced, result.task_failed
    calendar_synced, calendar_failed = result.calendar_synced, result.calendar_failed
    external_task_id = external_id_for(
        db, entity_type="task", entity_id=task.id, provider=adapter.provider,
        resource_type="task", legacy_id=task.google_task_id,
    )
    external_calendar_id = external_id_for(
        db, entity_type="task", entity_id=task.id, provider=adapter.provider,
        resource_type="calendar_event", legacy_id=task.google_calendar_event_id,
    )
    success = (not payload.publish_task or task_synced == 1 or bool(external_task_id)) and (
        not payload.publish_calendar or not task.due_date or calendar_synced == 1 or bool(external_calendar_id)
    )
    task.external_action_status = "executed" if success else "failed"
    db.add(AuditLog(action="external_task_action", entity_type="task", entity_id=task.id,
                    details=f"provider={adapter.provider}; task={task_synced}; calendar={calendar_synced}; success={success}"))
    db.commit(); db.refresh(task)
    return {"id": task.id, "provider": adapter.provider,
            "external_action_status": task.external_action_status,
            "google_task_id": external_task_id, "google_calendar_event_id": external_calendar_id,
            "external_resources": [
                *([{"provider": adapter.provider, "resource_type": "task", "external_id": external_task_id}] if external_task_id else []),
                *([{"provider": adapter.provider, "resource_type": "calendar_event", "external_id": external_calendar_id}] if external_calendar_id else []),
            ],
            "task_failed": task_failed, "google_task_failed": task_failed,
            "calendar_failed": calendar_failed}
