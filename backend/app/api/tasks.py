from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.task import Task
from app.models.user import User
from app.google_tasks import sync_tasks_to_google
from app.google_calendar import sync_tasks_to_calendar

router = APIRouter(prefix="/tasks", tags=["tasks"])


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
            "google_task_id": task.google_task_id, "google_sync_error": task.google_sync_error,
            "google_calendar_event_id": task.google_calendar_event_id,
            "google_calendar_sync_error": task.google_calendar_sync_error,
        }
        for task, assignee in rows
    ], "count": len(rows)}


@router.post("/sync-google")
def sync_google(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id, Task.google_task_id.is_(None))).all())
    synced, failed = sync_tasks_to_google(db, project_id, tasks)
    calendar_synced, calendar_failed = sync_tasks_to_calendar(db, project_id, tasks)
    return {"synced": synced, "failed": failed, "calendar_synced": calendar_synced, "calendar_failed": calendar_failed}
