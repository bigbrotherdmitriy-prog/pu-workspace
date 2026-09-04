import os
import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.token_crypto import TokenEncryptionError, decrypt_token, encrypt_token
from app.models.google_token import GoogleOAuthToken
from app.models.project import Project
from app.models.user import User
from app.core.auth import require_project_role, require_user
from app.integrations.google_workspace import credentials_for_project as _adapter_credentials_for_project


router = APIRouter(
    prefix="/projects",
    tags=["google-drive"],
)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _state_secret() -> bytes:
    value = os.getenv("APP_SECRET_KEY", "")
    if len(value) < 32:
        raise HTTPException(503, "APP_SECRET_KEY must contain at least 32 characters")
    return value.encode("utf-8")


def _make_oauth_state(project_id: int) -> str:
    payload = json.dumps(
        {"project_id": project_id, "expires": int(time.time()) + 600, "nonce": secrets.token_urlsafe(16)},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(_state_secret(), encoded, hashlib.sha256).digest()
    return (encoded + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode("ascii")


def _project_from_oauth_state(state: str) -> int:
    try:
        encoded, supplied = state.encode("ascii").split(b".", 1)
        expected = hmac.new(_state_secret(), encoded, hashlib.sha256).digest()
        signature = base64.urlsafe_b64decode(supplied + b"=" * (-len(supplied) % 4))
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4)))
        if int(payload["expires"]) < int(time.time()):
            raise ValueError("expired")
        return int(payload["project_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Invalid or expired OAuth state") from exc


def google_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured",
        )

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }, redirect_uri


def credentials_for_project(project_id: int, db: Session):
    """Backward-compatible facade; new code imports the integration adapter."""
    return _adapter_credentials_for_project(project_id, db)


def _fetch_google_credentials(flow: Flow, code: str):
    """Exchange an OAuth code and validate the granted capability set.

    Google can return a superset of the scopes requested in this flow when
    incremental authorization is enabled.  OAuthLib treats that legitimate
    superset as a fatal scope change unless its implicit check is disabled.
    We disable only that implicit comparison and then fail closed with an
    explicit subset check before any token is persisted.
    """
    flow.oauth2session.scope = None
    token_payload = flow.fetch_token(code=code)
    credentials = flow.credentials

    raw_scopes = token_payload.get("scope") or credentials.scopes or ()
    if isinstance(raw_scopes, str):
        granted_scopes = set(raw_scopes.split())
    else:
        granted_scopes = set(raw_scopes)

    missing_scopes = set(SCOPES) - granted_scopes
    if missing_scopes:
        raise HTTPException(
            status_code=400,
            detail="Google did not grant all required Workspace permissions",
        )
    return credentials


@router.get("/{project_id}/google/auth")
def google_auth(
    project_id: int,
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

    config, redirect_uri = google_config()

    flow = Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        # PU Workspace always requests its complete, fixed Workspace scope set.
        # Incremental consent can leave Google's reconnect screen waiting on an
        # older partial grant, so reconnect with the explicit set instead.
        include_granted_scopes="false",
        prompt="consent",
        state=_make_oauth_state(project_id),
    )

    return {
        "authorization_url": authorization_url,
        "project_id": project_id,
    }


@router.get("/google/callback")
def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    project_id = _project_from_oauth_state(state)

    project = db.get(Project, project_id)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    config, redirect_uri = google_config()

    flow = Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )

    credentials = _fetch_google_credentials(flow, code)

    token = db.scalar(
        select(GoogleOAuthToken).where(
            GoogleOAuthToken.project_id == project_id
        )
    )

    if token is None:
        token = GoogleOAuthToken(
            project_id=project_id,
        )
        db.add(token)

    try:
        token.access_token = encrypt_token(credentials.token)
        # Google may omit refresh_token on a repeat consent. Keep the last
        # encrypted refresh token instead of silently disconnecting the project
        # as soon as the new access token expires.
        if credentials.refresh_token:
            token.refresh_token = encrypt_token(credentials.refresh_token)
    except TokenEncryptionError as exc:
        raise HTTPException(503, str(exc)) from exc
    token.token_uri = credentials.token_uri
    token.scopes = " ".join(credentials.scopes or SCOPES)

    db.commit()

    return RedirectResponse(
        url=f"/new/?oauth=connected&project_id={project_id}"
    )


@router.get("/{project_id}/google/status")
def google_status(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    token = db.scalar(
        select(GoogleOAuthToken).where(
            GoogleOAuthToken.project_id == project_id
        )
    )

    return {
        "project_id": project_id,
        "authorized": bool(
            token and token.access_token
        ),
        "tasks_authorized": bool(token and "https://www.googleapis.com/auth/tasks" in (token.scopes or "").split()),
        "calendar_authorized": bool(token and "https://www.googleapis.com/auth/calendar.events" in (token.scopes or "").split()),
        "gmail_authorized": bool(token and {
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        }.issubset(set((token.scopes or "").split()))),
    }


@router.get("/{project_id}/google/files")
def google_files(
    project_id: int,
    folder_id: str = Query("root"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    credentials = credentials_for_project(
        project_id,
        db,
    )

    service = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    result = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="files(id,name,mimeType,parents,modifiedTime,size)",
            orderBy="folder,name",
        )
        .execute()
    )

    return {
        "folder_id": folder_id,
        "files": result.get("files", []),
    }

def drive_service_for_project(project_id: int, db):
    """Return authenticated Google Drive v3 service for organizer."""
    from googleapiclient.discovery import build
    creds = credentials_for_project(project_id=project_id, db=db)
    return build("drive", "v3", credentials=creds, cache_discovery=False)
