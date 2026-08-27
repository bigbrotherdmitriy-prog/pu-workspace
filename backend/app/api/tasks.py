from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.task import Task, TaskDueDateHistory
from app.models.user import User
from app.models.audit_log import AuditLog
from app.google_tasks import sync_tasks_to_google
from app.google_calendar import sync_tasks_to_calendar

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(assigned|in_progress|completed|cancelled)$")
    due_date: date | None = None
    due_change_reason: str | None = Field(default=None, max_length=2000)
    result_note: str | None = Field(default=None, max_length=5000)


class ExternalActionApproval(BaseModel):
    create_google_task: bool = True
    create_calendar_event: bool = True


@router.get("")
def list_tasks(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.execute(
        select(Task, User).join(User, User.id == Task.assignee_user_id)
        .where(Task.project_id == project_id).order_by(Task.created_at.desc(), Task.id.desc())
    ).all()
    return {"tasks": [
        {
            "id": task.id, "title": task.title, "status": task.status, "priority": task.priority,
            "due_date": task.due_date, "assignee_user_id": task.assignee_user_id,
            "assignee_name": assignee.name, "assignee_email": assignee.email,
            "source_file_name": task.source_file_name, "source_excerpt": task.source_excerpt,
            "confidence": task.confidence, "needs_review": task.needs_review,
            "message_id": task.message_id, "external_action_status": task.external_action_status,
            "google_task_id": task.google_task_id, "google_sync_error": task.google_sync_error,
            "google_calendar_event_id": task.google_calendar_event_id,
            "google_calendar_sync_error": task.google_calendar_sync_error,
            "result_note": task.result_note, "completed_at": task.completed_at,
        }
        for task, assignee in rows
    ], "count": len(rows)}


@router.patch("/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_project_role(db, user, task.project_id, "editor")
    if "due_date" in payload.model_fields_set and payload.due_date != task.due_date:
        if not (payload.due_change_reason or "").strip():
            raise HTTPException(422, "Причина переноса срока обязательна")
        db.add(TaskDueDateHistory(task_id=task.id, old_due_date=task.due_date, new_due_date=payload.due_date, reason=payload.due_change_reason.strip(), changed_by_user_id=user.id))
        task.due_date = payload.due_date
        task.google_calendar_event_id = None
        task.google_calendar_sync_error = None
    if payload.status:
        if payload.status == "completed" and not (payload.result_note or task.result_note or "").strip():
            raise HTTPException(422, "Для завершения задачи укажите подтверждаемый результат")
        task.status = payload.status
        task.completed_at = datetime.now(timezone.utc) if payload.status == "completed" else None
    if payload.result_note is not None:
        task.result_note = payload.result_note.strip() or None
    db.commit(); db.refresh(task)
    if task.external_action_status == "executed":
        sync_tasks_to_google(db, task.project_id, [task], force_update=True)
        sync_tasks_to_calendar(db, task.project_id, [task], force_update=True)
    return {"id": task.id, "status": task.status, "due_date": task.due_date, "result_note": task.result_note, "completed_at": task.completed_at}


@router.get("/{task_id}/due-history")
def due_history(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_project_role(db, user, task.project_id, "viewer")
    rows = db.scalars(select(TaskDueDateHistory).where(TaskDueDateHistory.task_id == task_id).order_by(TaskDueDateHistory.changed_at.desc())).all()
    return {"history": [{"old_due_date": x.old_due_date, "new_due_date": x.new_due_date, "reason": x.reason, "changed_at": x.changed_at} for x in rows]}


@router.post("/sync-google")
def sync_google(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id, Task.external_action_status == "approved", Task.google_task_id.is_(None))).all())
    synced, failed = sync_tasks_to_google(db, project_id, tasks)
    calendar_synced, calendar_failed = sync_tasks_to_calendar(db, project_id, tasks)
    return {"synced": synced, "failed": failed, "calendar_synced": calendar_synced, "calendar_failed": calendar_failed}


@router.post("/{task_id}/approve-external")
def approve_external(task_id: int, payload: ExternalActionApproval, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    require_project_role(db, user, task.project_id, "manager")
    if not payload.create_google_task and not payload.create_calendar_event:
        raise HTTPException(422, "Select at least one external action")
    if task.message_id is not None and task.needs_review:
        task.needs_review = False
    task.external_action_status = "approved"
    db.commit()
    task_synced = task_failed = calendar_synced = calendar_failed = 0
    if payload.create_google_task:
        task_synced, task_failed = sync_tasks_to_google(db, task.project_id, [task])
    if payload.create_calendar_event and task.due_date:
        calendar_synced, calendar_failed = sync_tasks_to_calendar(db, task.project_id, [task])
    success = (not payload.create_google_task or task_synced == 1 or bool(task.google_task_id)) and (
        not payload.create_calendar_event or not task.due_date or calendar_synced == 1 or bool(task.google_calendar_event_id)
    )
    task.external_action_status = "executed" if success else "failed"
    db.add(AuditLog(action="external_task_action", entity_type="task", entity_id=task.id,
                    details=f"google_task={task_synced}; calendar={calendar_synced}; success={success}"))
    db.commit(); db.refresh(task)
    return {"id": task.id, "external_action_status": task.external_action_status,
            "google_task_id": task.google_task_id, "google_calendar_event_id": task.google_calendar_event_id,
            "google_task_failed": task_failed, "calendar_failed": calendar_failed}
