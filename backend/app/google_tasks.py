from __future__ import annotations
from datetime import datetime, time, timezone
from googleapiclient.discovery import build
from sqlalchemy.orm import Session
from app.integrations.google_workspace import google_workspace_for_project
from app.integrations.external_resources import external_id_for, get_external_resource, record_external_resource
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


def sync_tasks_to_google(db: Session, project_id: int, tasks: list[Task], force_update: bool = False) -> tuple[int, int]:
    linked = {
        task.id: external_id_for(
            db, entity_type="task", entity_id=task.id, provider="google_workspace",
            resource_type="task", legacy_id=task.google_task_id,
        ) for task in tasks
    }
    pending = [task for task in tasks if force_update or not linked[task.id]]
    if not pending:
        return 0, 0
    try:
        credentials = google_workspace_for_project(project_id, db).credentials()
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
            body = task_payload(task)
            body["status"] = "completed" if task.status == "completed" else "needsAction"
            external_id = linked[task.id]
            existing_link = get_external_resource(
                db, entity_type="task", entity_id=task.id, provider="google_workspace", resource_type="task",
            )
            container_id = (existing_link.container_id if existing_link else None) or task.google_task_list_id or "@default"
            if external_id:
                result = service.tasks().patch(tasklist=container_id, task=external_id, body=body).execute()
            else:
                result = service.tasks().insert(tasklist="@default", body=body).execute()
                external_id = result["id"]
            task.google_task_id = external_id
            task.google_task_list_id = container_id
            task.google_sync_error = None
            task.google_synced_at = datetime.now(timezone.utc)
            record_external_resource(
                db, project_id=project_id, entity_type="task", entity_id=task.id,
                provider="google_workspace", resource_type="task",
                external_id=external_id, container_id=container_id,
            )
            synced += 1
        except Exception as exc:
            task.google_sync_error = str(exc)[:1000]
            failed += 1
    db.commit()
    return synced, failed
