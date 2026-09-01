from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token
from app.integrations.contracts import StorageAdapter
from app.integrations.google_workspace import google_workspace_for_project
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.integrations.yandex_oauth import credential_expiring, refresh_yandex_credential
from app.models.drive_connection import DriveConnection
from app.models.integration_credential import IntegrationCredential
from app.organizer_engine.drive import DriveClient


def project_storage_connection(project_id: int, db: Session) -> DriveConnection | None:
    return db.scalar(select(DriveConnection).where(DriveConnection.project_id == project_id))


def storage_for_project(project_id: int, db: Session) -> StorageAdapter:
    """Resolve the selected provider outside Project and Document Core.

    Missing legacy connection rows continue to resolve to Google exactly as
    before. Existing Google tokens and folder identifiers are never rewritten.
    """
    connection = project_storage_connection(project_id, db)
    provider = connection.provider if connection else "google_drive"
    if provider in {"google_drive", "google_workspace"}:
        return DriveClient(google_workspace_for_project(project_id, db).service("drive", "v3"))
    if provider == "yandex_disk":
        credential = db.scalar(select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.provider == "yandex_disk",
            IntegrationCredential.capability == "storage",
        ))
        token = refresh_yandex_credential(credential, db) if credential and credential_expiring(credential) else (decrypt_token(credential.access_token) if credential else None)
        if not token:
            raise HTTPException(401, "Yandex Disk is not authorized")
        return YandexDiskStorageAdapter(token)
    raise HTTPException(503, f"Storage provider '{provider}' is unavailable")
