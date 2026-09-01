from fastapi import APIRouter, Depends, HTTPException
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.drive_connection import DriveConnection
from app.models.project import Project
from app.models.user import User
from app.core.auth import require_project_role, require_user


router = APIRouter(
    prefix="/projects",
    tags=["drive"],
)


class DriveConnectRequest(BaseModel):
    account_email: str
    root_folder_id: str
    provider: Literal["google_drive", "yandex_disk"] = "google_drive"
    connection_id: str | None = None
    root_display_name: str | None = None
    sync_settings: dict = Field(default_factory=dict)


@router.put("/{project_id}/drive")
def connect_drive(
    project_id: int,
    payload: DriveConnectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    project = db.get(Project, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    connection = db.scalar(
        select(DriveConnection).where(
            DriveConnection.project_id == project_id
        )
    )

    if connection is None:
        connection = DriveConnection(
            project_id=project_id,
            account_email=payload.account_email,
            root_folder_id=payload.root_folder_id,
            provider=payload.provider,
            status="configured",
        )
        db.add(connection)
    else:
        connection.account_email = payload.account_email
        connection.root_folder_id = payload.root_folder_id
        connection.provider = payload.provider
        connection.status = "configured"

    connection.connection_id = payload.connection_id
    connection.root_display_name = payload.root_display_name
    import json
    connection.sync_settings = json.dumps(payload.sync_settings, ensure_ascii=False, separators=(",", ":"))

    db.commit()
    db.refresh(connection)

    return {
        "id": connection.id,
        "project_id": connection.project_id,
        "provider": connection.provider,
        "account_email": connection.account_email,
        "root_folder_id": connection.root_folder_id,
        "status": connection.status,
        "connection_id": connection.connection_id,
        "root_display_name": connection.root_display_name,
        "sync_settings": payload.sync_settings,
    }


@router.get("/{project_id}/drive")
def get_drive(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    connection = db.scalar(
        select(DriveConnection).where(
            DriveConnection.project_id == project_id
        )
    )

    if connection is None:
        raise HTTPException(
            status_code=404,
            detail="Drive connection not configured",
        )

    return {
        "id": connection.id,
        "project_id": connection.project_id,
        "provider": connection.provider,
        "account_email": connection.account_email,
        "root_folder_id": connection.root_folder_id,
        "status": connection.status,
        "connection_id": connection.connection_id,
        "root_display_name": connection.root_display_name,
        "sync_settings": __import__("json").loads(connection.sync_settings or "{}"),
    }
