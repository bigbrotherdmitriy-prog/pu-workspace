from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.core.oauth_state import make_oauth_state, project_from_oauth_state
from app.core.token_crypto import TokenEncryptionError, decrypt_token, encrypt_token
from app.database import get_db
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.integrations.yandex_oauth import TOKEN_URL, credential_expiring, refresh_yandex_credential
from app.models.audit_log import AuditLog
from app.models.drive_connection import DriveConnection
from app.models.integration_credential import IntegrationCredential
from app.models.project import Project
from app.models.user import User


router = APIRouter(prefix="/projects", tags=["yandex-disk"])
AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
REVOKE_URL = "https://oauth.yandex.ru/revoke_token"


class YandexRootRequest(BaseModel):
    root_locator: str
    display_name: str
    sync_settings: dict = Field(default_factory=dict)


def _config() -> tuple[str, str, str]:
    values = tuple(os.getenv(name, "").strip() for name in ("YANDEX_CLIENT_ID", "YANDEX_CLIENT_SECRET", "YANDEX_REDIRECT_URI"))
    if not all(values):
        raise HTTPException(503, "Yandex OAuth is not configured")
    return values  # type: ignore[return-value]


def _credential(project_id: int, db: Session) -> IntegrationCredential | None:
    return db.scalar(select(IntegrationCredential).where(
        IntegrationCredential.project_id == project_id,
        IntegrationCredential.provider == "yandex_disk",
        IntegrationCredential.capability == "storage",
    ))


def _token_for_project(project_id: int, db: Session) -> str:
    row = _credential(project_id, db)
    if not row or not row.access_token:
        raise HTTPException(401, "Yandex Disk is not authorized")
    try:
        token = decrypt_token(row.access_token)
    except TokenEncryptionError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not token:
        raise HTTPException(401, "Yandex Disk is not authorized")
    if credential_expiring(row):
        token = _refresh(project_id, db)
    return token


def _refresh(project_id: int, db: Session) -> str:
    row = _credential(project_id, db)
    if not row:
        raise HTTPException(401, "Yandex refresh token is unavailable; reconnect the account")
    return refresh_yandex_credential(row, db)


@router.get("/{project_id}/yandex/auth")
def yandex_auth(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    client_id, _, redirect_uri = _config()
    query = urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "state": make_oauth_state(project_id, "yandex_disk"), "force_confirm": "yes",
    })
    return {"authorization_url": f"{AUTHORIZE_URL}?{query}", "project_id": project_id}


@router.get("/yandex/callback")
def yandex_callback(code: str = Query(...), state: str = Query(...), db: Session = Depends(get_db)):
    project_id = project_from_oauth_state(state, "yandex_disk")
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    client_id, client_secret, redirect_uri = _config()
    response = httpx.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "client_secret": client_secret, "redirect_uri": redirect_uri,
    }, timeout=20.0)
    if response.status_code >= 400:
        raise HTTPException(400, "Yandex OAuth rejected the authorization code")
    payload = response.json()
    access_token = payload["access_token"]
    profile_response = httpx.get("https://cloud-api.yandex.net/v1/disk", headers={"Authorization": f"OAuth {access_token}"}, params={"fields": "user"}, timeout=20.0)
    profile_response.raise_for_status()
    profile = profile_response.json().get("user") or {}
    row = _credential(project_id, db) or IntegrationCredential(project_id=project_id, provider="yandex_disk", capability="storage")
    row.access_token = encrypt_token(access_token)
    row.refresh_token = encrypt_token(payload.get("refresh_token"))
    row.token_uri = TOKEN_URL
    row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in") or 0)) if payload.get("expires_in") else None
    row.scopes = "cloud_api:disk.read cloud_api:disk.write"
    row.account_external_id = str(profile.get("uid") or profile.get("login") or "")
    row.account_email = profile.get("login") or profile.get("display_name")
    db.add(row)
    db.add(AuditLog(action="storage_oauth_connected", entity_type="project", entity_id=project_id, details="provider=yandex_disk"))
    db.commit()
    return RedirectResponse(url=f"/new/?oauth=connected&provider=yandex_disk&project_id={project_id}")


@router.get("/{project_id}/yandex/status")
def yandex_status(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    row = _credential(project_id, db)
    health = YandexDiskStorageAdapter(_token_for_project(project_id, db)).health() if row and row.access_token else None
    connection = db.scalar(select(DriveConnection).where(DriveConnection.project_id == project_id))
    return {
        "project_id": project_id, "provider": "yandex_disk", "authorized": bool(row and row.access_token),
        "connected": bool(health and health.ready), "detail": health.detail if health else "authorization required",
        "selected_for_project": bool(connection and connection.provider == "yandex_disk"),
        "account_email": row.account_email if row else None,
    }


@router.post("/{project_id}/yandex/refresh")
def yandex_refresh(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    _refresh(project_id, db)
    return {"refreshed": True}


@router.delete("/{project_id}/yandex")
def yandex_disconnect(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    row = _credential(project_id, db)
    connection = db.scalar(select(DriveConnection).where(DriveConnection.project_id == project_id))
    if connection and connection.provider == "yandex_disk":
        connection.status = "disconnected"
    if row:
        # Regular web tokens may not be remotely revocable. Local encrypted
        # credentials are always deleted; device tokens are revoked best-effort.
        try:
            client_id, client_secret, _ = _config()
            access_token = decrypt_token(row.access_token)
            if access_token:
                httpx.post(REVOKE_URL, data={"access_token": access_token, "client_id": client_id, "client_secret": client_secret}, timeout=10.0)
        except Exception:
            pass
        db.delete(row)
    db.add(AuditLog(action="storage_oauth_disconnected", entity_type="project", entity_id=project_id, details="provider=yandex_disk"))
    db.commit()
    return {"disconnected": True}


@router.get("/{project_id}/yandex/files")
def yandex_files(project_id: int, folder_id: str = Query("disk:/"), db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    adapter = YandexDiskStorageAdapter(_token_for_project(project_id, db))
    files = adapter.list_children(folder_id)
    return {"folder_id": adapter.normalize_locator(folder_id), "files": [asdict(item) for item in files]}


@router.put("/{project_id}/yandex/root")
def select_yandex_root(project_id: int, payload: YandexRootRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    row = _credential(project_id, db)
    if not row:
        raise HTTPException(401, "Connect Yandex Disk first")
    locator = YandexDiskStorageAdapter.normalize_locator(payload.root_locator)
    adapter = YandexDiskStorageAdapter(_token_for_project(project_id, db))
    folder = adapter.get_object(locator)
    if not folder.is_folder:
        raise HTTPException(400, "Project root must be a folder")
    connection = db.scalar(select(DriveConnection).where(DriveConnection.project_id == project_id))
    if connection is None:
        connection = DriveConnection(project_id=project_id, provider="yandex_disk", account_email=row.account_email or "", root_folder_id=locator)
        db.add(connection)
    connection.provider = "yandex_disk"
    connection.connection_id = str(row.id)
    connection.account_email = row.account_email or ""
    connection.root_folder_id = locator
    connection.root_display_name = payload.display_name or folder.name
    connection.sync_settings = json.dumps(payload.sync_settings, ensure_ascii=False, separators=(",", ":"))
    connection.status = "connected"
    db.add(AuditLog(action="storage_root_selected", entity_type="project", entity_id=project_id, details=f"provider=yandex_disk root={locator}"))
    db.commit()
    return {"provider": connection.provider, "connection_id": connection.connection_id, "root_locator": locator, "root_display_name": connection.root_display_name, "sync_settings": payload.sync_settings}
