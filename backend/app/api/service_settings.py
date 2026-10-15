from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.project_service_settings import ProjectServiceSettings
from app.models.user import User
from app.service_settings import get_settings

router = APIRouter(tags=["service-settings"])

_ALLOWED_PRIMARY_MAIL = {"gmail", "yandex_mail"}


class ServiceSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gmail_enabled: bool | None = None
    google_tasks_enabled: bool | None = None
    google_calendar_enabled: bool | None = None
    primary_mail: str | None = None


class ServiceSettingsResponse(BaseModel):
    project_id: int
    gmail_enabled: bool
    google_tasks_enabled: bool
    google_calendar_enabled: bool
    primary_mail: str | None


def _serialize(project_id: int, settings) -> ServiceSettingsResponse:
    return ServiceSettingsResponse(
        project_id=project_id,
        gmail_enabled=settings.gmail_enabled,
        google_tasks_enabled=settings.google_tasks_enabled,
        google_calendar_enabled=settings.google_calendar_enabled,
        primary_mail=settings.primary_mail,
    )


@router.get("/projects/{project_id}/service-settings", response_model=ServiceSettingsResponse)
def read_service_settings(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    return _serialize(project_id, get_settings(db, project_id))


@router.put("/projects/{project_id}/service-settings", response_model=ServiceSettingsResponse)
def update_service_settings(
    project_id: int,
    payload: ServiceSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")

    data = payload.model_dump(exclude_unset=True)

    if "primary_mail" in data:
        value = data["primary_mail"]
        if value is not None and value not in _ALLOWED_PRIMARY_MAIL:
            raise HTTPException(status_code=422, detail="invalid primary_mail")

    row = db.get(ProjectServiceSettings, project_id)
    if row is None:
        row = ProjectServiceSettings(project_id=project_id)
        db.add(row)

    if "gmail_enabled" in data and data["gmail_enabled"] is not None:
        row.gmail_enabled = data["gmail_enabled"]
    if "google_tasks_enabled" in data and data["google_tasks_enabled"] is not None:
        row.google_tasks_enabled = data["google_tasks_enabled"]
    if "google_calendar_enabled" in data and data["google_calendar_enabled"] is not None:
        row.google_calendar_enabled = data["google_calendar_enabled"]
    if "primary_mail" in data:
        row.primary_mail = data["primary_mail"]
        if data["primary_mail"] == "gmail":
            row.gmail_enabled = True

    db.commit()
    db.refresh(row)

    return _serialize(project_id, get_settings(db, project_id))
