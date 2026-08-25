from __future__ import annotations
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from sqlalchemy.orm import Session
from app.api.google_drive import credentials_for_project
from app.models.task import Task

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def event_payload(task: Task) -> dict:
    if not task.due_date:
        raise ValueError("Calendar event requires a due date")
    return {
        "summary": f"PU Workspace: {task.title}"[:1024],
        "description": (
            f"Источник: {task.source_file_name}\n"
            f"Основание: {task.source_excerpt}\n"
            f"Уверенность: {round(task.confidence * 100)}%\n"
            "Проверьте формулировку по исходному документу."
        ),
        "start": {"date": task.due_date.isoformat()},
        "end": {"date": (task.due_date + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
    }


def sync_tasks_to_calendar(db: Session, project_id: int, tasks: list[Task]) -> tuple[int, int]:
    pending = [task for task in tasks if task.due_date and not task.google_calendar_event_id]
    if not pending:
        return 0, 0
    try:
        credentials = credentials_for_project(project_id, db)
        if CALENDAR_SCOPE not in set(credentials.scopes or []):
            raise RuntimeError("Требуется повторно подключить Google и разрешить Google Calendar")
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        for task in pending:
            task.google_calendar_sync_error = str(exc)[:1000]
        db.commit()
        return 0, len(pending)
    synced = failed = 0
    for task in pending:
        try:
            result = service.events().insert(calendarId="primary", body=event_payload(task)).execute()
            task.google_calendar_event_id = result["id"]
            task.google_calendar_sync_error = None
            task.google_calendar_synced_at = datetime.now(timezone.utc)
            synced += 1
        except Exception as exc:
            task.google_calendar_sync_error = str(exc)[:1000]
            failed += 1
    db.commit()
    return synced, failed
