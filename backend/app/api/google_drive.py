import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.token_crypto import TokenEncryptionError, decrypt_token, encrypt_token
from app.core.oauth_state import make_oauth_state, project_from_oauth_state
from app.models.google_token import GoogleOAuthToken
from app.models.project import Project
from app.models.user import User
from app.core.auth import require_project_role, require_user
from app.integrations.google_workspace import credentials_for_project as _adapter_credentials_for_project
from app.mailbox_identity.oauth import OIDCVerificationError, verified_google_subject
from app.mailbox_identity.service import MailboxConflict, MailboxIdentityService


router = APIRouter(
    prefix="/projects",
    tags=["google-drive"],
)

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _make_oauth_state(project_id: int) -> str:
    return make_oauth_state(project_id, "google")


def _project_from_oauth_state(state: str) -> int:
    return project_from_oauth_state(state, "google")


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
        include_granted_scopes="true",
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

    flow.fetch_token(code=code)

    credentials = flow.credentials

    # Verify signed OIDC identity before mutating any credential row.  Email,
    # project and token-row identity are deliberately not accepted as mailbox identity.
    try:
        account_subject = verified_google_subject(credentials.id_token, config["web"]["client_id"])
    except OIDCVerificationError as exc:
        raise HTTPException(401, "Google account identity could not be verified") from exc

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
        db.flush()

    try:
        token.access_token = encrypt_token(credentials.token)
        token.refresh_token = encrypt_token(credentials.refresh_token)
    except TokenEncryptionError as exc:
        raise HTTPException(503, str(exc)) from exc
    token.token_uri = credentials.token_uri
    token.scopes = " ".join(credentials.scopes or SCOPES)

    try:
        MailboxIdentityService().bind_verified_google_subject(
            db, organization_id=project.organization_id,
            google_token_id=token.id, subject=account_subject,
        )
    except MailboxConflict as exc:
        db.rollback()
        raise HTTPException(409, "Explicit mailbox revoke is required") from exc

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
