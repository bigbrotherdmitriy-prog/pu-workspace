from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token
from app.integrations.contracts import StorageAdapter
from app.integrations.google_workspace import google_workspace_for_project
from app.integrations.google_storage_credentials import google_storage_service
from app.integrations.storage_credentials import DatabaseStorageCredentialPort, StorageCredentialError
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.integrations.yandex_oauth import credential_expiring, refresh_yandex_credential
from app.models.drive_connection import DriveConnection
from app.models.integration_credential import IntegrationCredential
from app.models.google_token import GoogleOAuthToken
from app.models.project import Project
from app.organizer_engine.drive import DriveClient


def project_storage_connection(project_id: int, db: Session) -> DriveConnection | None:
    return db.scalar(select(DriveConnection).where(DriveConnection.project_id == project_id))


def validate_storage_locator(provider: str, locator: str) -> str:
    """Validate a selected locator without translating it into another provider."""
    if not locator or len(locator) > 255 or any(ord(char) < 32 for char in locator):
        raise HTTPException(422, "Invalid or unsupported folder locator length")
    if provider == "yandex_disk":
        if not locator.startswith(("disk:/", "app:/")) or any(part in {".", ".."} for part in locator.split("/")):
            raise HTTPException(422, "Yandex folder selection requires a disk:/ or app:/ path")
    elif provider in {"google_drive", "google_workspace"}:
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in locator):
            raise HTTPException(422, "Google folder selection requires an opaque Drive ID")
    else:
        raise HTTPException(409, "Selected storage provider is unavailable")
    return locator


def storage_for_project(project_id: int, db: Session) -> StorageAdapter:
    """Resolve the selected provider outside Project and Document Core.

    Missing legacy connection rows continue to resolve to Google exactly as
    before. Existing Google tokens and folder identifiers are never rewritten.
    """
    connection = project_storage_connection(project_id, db)
    if connection and connection.status == "disconnected":
        raise HTTPException(409, "Project storage is disconnected")
    provider = connection.provider if connection else "google_drive"
    if provider in {"google_drive", "google_workspace"}:
        if connection and connection.connection_id:
            if connection.connection_id.startswith("storage-credential:"):
                project = db.get(Project, project_id)
                if project is None:
                    raise HTTPException(404, "Project not found")
                try:
                    resolved = DatabaseStorageCredentialPort().resolve(
                        db, connection_id=connection.connection_id, project_id=project_id,
                        organization_id=project.organization_id, provider="google_drive",
                    )
                except StorageCredentialError as exc:
                    raise HTTPException(409, "Selected storage connection no longer matches project credentials") from exc
                return DriveClient(google_storage_service(resolved, db))
            if connection.connection_id.startswith("google-token:"):
                raw_id = connection.connection_id.removeprefix("google-token:")
                if not raw_id.isdigit() or str(int(raw_id)) != raw_id:
                    raise HTTPException(409, "Selected storage connection is invalid")
                token = db.scalar(select(GoogleOAuthToken).where(
                    GoogleOAuthToken.id == int(raw_id), GoogleOAuthToken.project_id == project_id,
                ))
                if token is None:
                    raise HTTPException(409, "Selected storage connection no longer matches project credentials")
                from app.integrations.google_workspace import GoogleWorkspaceAdapter
                return DriveClient(GoogleWorkspaceAdapter(project_id, db, token_id=token.id).service("drive", "v3"))
            raise HTTPException(409, "Selected Google storage connection is not an authenticated credential")
        # Legacy compatibility only for rows created before connection_id was recorded.
        return DriveClient(google_workspace_for_project(project_id, db).service("drive", "v3"))
    if provider == "yandex_disk":
        credential = db.scalar(select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.provider == "yandex_disk",
            IntegrationCredential.capability == "storage",
        ))
        if connection.connection_id is not None and (credential is None or connection.connection_id != str(credential.id)):
            raise HTTPException(409, "Selected storage connection no longer matches project credentials")
        token = refresh_yandex_credential(credential, db) if credential and credential_expiring(credential) else (decrypt_token(credential.access_token) if credential else None)
        if not token:
            raise HTTPException(401, "Yandex Disk is not authorized")
        return YandexDiskStorageAdapter(token)
    raise HTTPException(503, f"Storage provider '{provider}' is unavailable")
