from __future__ import annotations
from datetime import datetime, time, timezone
from googleapiclient.discovery import build
from sqlalchemy.orm import Session
from app.api.google_drive import credentials_for_project
from app.models.task import Task

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"


def task_payload(task: Task) -> dict:
    notes = (
        f"Источник: {task.source_file_name}\n"
        f"Основание: {task.source_excerpt}\n"
        f"Уверенность PU Workspace: {round(task.confidence * 100)}%\n"
        "Перед выполнением проверьте формулировку по исходному документу."
    )
    payload = {"title": task.title[:1024], "notes": notes}
    if task.due_date:
        payload["due"] = datetime.combine(task.due_date, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return payload


def sync_tasks_to_google(db: Session, project_id: int, tasks: list[Task]) -> tuple[int, int]:
    pending = [task for task in tasks if not task.google_task_id]
    if not pending:
        return 0, 0
    try:
        credentials = credentials_for_project(project_id, db)
        if TASKS_SCOPE not in set(credentials.scopes or []):
            raise RuntimeError("Требуется повторно подключить Google и разрешить Google Tasks")
        service = build("tasks", "v1", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        for task in pending:
            task.google_sync_error = str(exc)[:1000]
        db.commit()
        return 0, len(pending)
    synced = failed = 0
    for task in pending:
        try:
            result = service.tasks().insert(tasklist="@default", body=task_payload(task)).execute()
            task.google_task_id = result["id"]
            task.google_task_list_id = "@default"
            task.google_sync_error = None
            task.google_synced_at = datetime.now(timezone.utc)
            synced += 1
        except Exception as exc:
            task.google_sync_error = str(exc)[:1000]
            failed += 1
    db.commit()
    return synced, failed
