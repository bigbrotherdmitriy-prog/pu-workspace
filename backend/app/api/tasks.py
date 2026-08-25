from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.task import Task
from app.models.user import User

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
        }
        for task, assignee in rows
    ], "count": len(rows)}
