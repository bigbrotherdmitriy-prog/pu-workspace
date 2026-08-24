import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.google_token import GoogleOAuthToken
from app.models.project import Project


router = APIRouter(
    prefix="/projects",
    tags=["google-drive"],
)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


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
    token = db.scalar(
        select(GoogleOAuthToken).where(
            GoogleOAuthToken.project_id == project_id
        )
    )

    if token is None or token.access_token is None:
        raise HTTPException(
            status_code=401,
            detail="Google Drive is not authorized",
        )

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    credentials = Credentials(
        token=token.access_token,
        refresh_token=token.refresh_token,
        token_uri=token.token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=(token.scopes or "").split(),
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

        token.access_token = credentials.token

        if credentials.refresh_token:
            token.refresh_token = credentials.refresh_token

        db.commit()

    return credentials


@router.get("/{project_id}/google/auth")
def google_auth(
    project_id: int,
    db: Session = Depends(get_db),
):
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
        state=str(project_id),
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
    try:
        project_id = int(state)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state",
        )

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

    token.access_token = credentials.token
    token.refresh_token = credentials.refresh_token
    token.token_uri = credentials.token_uri
    token.scopes = " ".join(credentials.scopes or SCOPES)

    db.commit()

    return RedirectResponse(
        url=f"/projects/{project_id}/google/status"
    )


@router.get("/{project_id}/google/status")
def google_status(
    project_id: int,
    db: Session = Depends(get_db),
):
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
    }


@router.get("/{project_id}/google/files")
def google_files(
    project_id: int,
    folder_id: str = Query("root"),
    db: Session = Depends(get_db),
):
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
