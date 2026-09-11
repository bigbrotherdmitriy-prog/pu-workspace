"""MVP-1-only Google Drive OAuth port; no Gmail/mailbox capabilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from google.oauth2 import id_token as google_id_token
from google.auth.transport.requests import Request as GoogleRequest
from google_auth_oauthlib.flow import Flow
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.core.token_crypto import TokenEncryptionError, encrypt_token
from app.database import get_db
from app.integrations.storage_credentials import (
    DatabaseStorageCredentialPort, StorageCredentialScopeMismatch,
)
from app.models.audit_log import AuditLog
from app.models.drive_connection import DriveConnection
from app.models.project import Project
from app.models.storage_oauth_state import StorageOAuthState
from app.models.user import User


router = APIRouter(prefix="/projects", tags=["mvp1-google-storage-oauth"])
GOOGLE_STORAGE_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/drive",
)
GOOGLE_EMAIL_SCOPE_ALIASES = frozenset({
    "email",
    "https://www.googleapis.com/auth/userinfo.email",
})
STATE_TTL = timedelta(minutes=10)


def _config() -> tuple[dict, str]:
    client_id = (os.getenv("GOOGLE_STORAGE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_STORAGE_CLIENT_SECRET") or "").strip()
    redirect_uri = (os.getenv("GOOGLE_STORAGE_REDIRECT_URI") or "").strip()
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(503, "MVP-1 Google Drive OAuth is not configured")
    return ({"web": {
        "client_id": client_id, "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri],
    }}, redirect_uri)


def _state_hash(state: str) -> str:
    if not state or len(state) > 256:
        raise HTTPException(400, "Invalid or expired OAuth state")
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _fetch_credentials(flow: Flow, code: str):
    flow.oauth2session.scope = None
    payload = flow.fetch_token(code=code)
    credentials = flow.credentials
    raw_scopes = payload.get("scope") or credentials.scopes or ()
    granted = set(raw_scopes.split() if isinstance(raw_scopes, str) else raw_scopes)
    required = {"openid", "https://www.googleapis.com/auth/drive"}
    if required - granted or not (GOOGLE_EMAIL_SCOPE_ALIASES & granted):
        raise HTTPException(400, "Google did not grant the required Drive permissions")
    return credentials


def _verified_account(credentials, client_id: str) -> tuple[str, str | None]:
    try:
        claims = google_id_token.verify_oauth2_token(
            credentials.id_token, GoogleRequest(), audience=client_id,
        )
    except Exception as exc:
        raise HTTPException(401, "Google account identity could not be verified") from exc
    subject = str(claims.get("sub") or "")
    email = str(claims.get("email") or "") or None
    if not subject or claims.get("email_verified") is not True:
        raise HTTPException(401, "Google account identity could not be verified")
    return subject, email


def _bind_credential(
    db: Session, *, project: Project, connection_id: str, account_email: str | None,
) -> DriveConnection:
    connection = db.scalar(select(DriveConnection).where(
        DriveConnection.project_id == project.id,
    ).with_for_update())
    if connection is None:
        connection = DriveConnection(
            project_id=project.id,
            provider="google_drive",
            account_email=account_email or "verified-google-account",
            root_folder_id="root",
            root_display_name="Google Drive",
            connection_id=connection_id,
            status="connected",
        )
        db.add(connection)
    else:
        # Reauthorization changes only identity/credential state. Preserve the
        # exact nested folder previously selected by the user.
        connection.provider = "google_drive"
        connection.account_email = account_email or "verified-google-account"
        connection.connection_id = connection_id
        connection.status = "connected"
    return connection


@router.get("/{project_id}/storage/google/oauth")
def google_storage_auth(project_id: int, db: Session = Depends(get_db),
                        user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    config, redirect_uri = _config()
    raw_state = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    db.execute(delete(StorageOAuthState).where(StorageOAuthState.expires_at <= now))
    db.add(StorageOAuthState(
        state_hash=_state_hash(raw_state), organization_id=project.organization_id,
        project_id=project.id, user_id=user.id, provider="google_drive",
        created_at=now, expires_at=now + STATE_TTL,
    ))
    db.commit()
    flow = Flow.from_client_config(config, scopes=GOOGLE_STORAGE_SCOPES, redirect_uri=redirect_uri)
    authorization_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="false", prompt="consent",
        state=raw_state,
    )
    return {"authorization_url": authorization_url, "project_id": project_id}


@router.get("/storage/google/callback")
def google_storage_callback(code: str = Query(...), state: str = Query(...),
                            db: Session = Depends(get_db), user: User = Depends(require_user)):
    now = datetime.now(timezone.utc)
    state_row = db.scalar(select(StorageOAuthState).where(
        StorageOAuthState.state_hash == _state_hash(state),
    ).with_for_update())
    if (state_row is None or state_row.provider != "google_drive"
            or state_row.user_id != user.id or _utc(state_row.expires_at) <= now
            or state_row.consumed_at is not None):
        raise HTTPException(400, "Invalid or expired OAuth state")
    project = db.scalar(select(Project).where(
        Project.id == state_row.project_id,
        Project.organization_id == state_row.organization_id,
    ).with_for_update())
    if project is None:
        raise HTTPException(404, "Project not found")
    require_project_role(db, user, project.id, "manager")
    config, redirect_uri = _config()
    flow = Flow.from_client_config(config, scopes=GOOGLE_STORAGE_SCOPES, redirect_uri=redirect_uri)
    credentials = _fetch_credentials(flow, code)
    subject, email = _verified_account(credentials, config["web"]["client_id"])
    try:
        resolved = DatabaseStorageCredentialPort().store(
            db, project_id=project.id, organization_id=project.organization_id,
            provider="google_drive", access_token=encrypt_token(credentials.token),
            refresh_token=encrypt_token(credentials.refresh_token),
            token_uri=credentials.token_uri or "https://oauth2.googleapis.com/token",
            scopes=tuple(credentials.scopes or GOOGLE_STORAGE_SCOPES),
            expires_at=credentials.expiry, account_external_id=subject, account_email=email,
        )
    except (TokenEncryptionError, StorageCredentialScopeMismatch) as exc:
        db.rollback()
        raise HTTPException(503, "Google Drive credential could not be stored") from exc
    _bind_credential(
        db, project=project, connection_id=resolved.connection_id, account_email=email,
    )
    state_row.consumed_at = now
    db.add(AuditLog(
        action="mvp1_storage_oauth_connected", entity_type="project", entity_id=project.id,
        details=f"provider=google_drive; credential_id={resolved.credential_id}; user={user.id}",
    ))
    db.commit()
    return RedirectResponse(
        url=(f"/new/?oauth=connected&provider=google_drive&project_id={project.id}"
             f"&connection_id={resolved.connection_id}")
    )
