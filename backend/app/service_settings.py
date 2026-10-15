from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.project_service_settings import ProjectServiceSettings


@dataclass(frozen=True)
class ServiceSettings:
    gmail_enabled: bool = False
    google_tasks_enabled: bool = False
    google_calendar_enabled: bool = False
    primary_mail: str | None = None


def get_settings(db: Session, project_id: int) -> ServiceSettings:
    row = db.get(ProjectServiceSettings, project_id)
    if row is None:
        return ServiceSettings()
    return ServiceSettings(
        gmail_enabled=bool(row.gmail_enabled),
        google_tasks_enabled=bool(row.google_tasks_enabled),
        google_calendar_enabled=bool(row.google_calendar_enabled),
        primary_mail=row.primary_mail,
    )


def gmail_active(db: Session, project_id: int) -> bool:
    settings = get_settings(db, project_id)
    if not settings.gmail_enabled:
        return False
    return settings.primary_mail in (None, "gmail")
