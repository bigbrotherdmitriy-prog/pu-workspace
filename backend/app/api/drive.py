from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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
            provider="google_drive",
            status="configured",
        )
        db.add(connection)
    else:
        connection.account_email = payload.account_email
        connection.root_folder_id = payload.root_folder_id
        connection.status = "configured"

    db.commit()
    db.refresh(connection)

    return {
        "id": connection.id,
        "project_id": connection.project_id,
        "provider": connection.provider,
        "account_email": connection.account_email,
        "root_folder_id": connection.root_folder_id,
        "status": connection.status,
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
    }
