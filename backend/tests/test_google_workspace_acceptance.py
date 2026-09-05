from types import SimpleNamespace

from cryptography.fernet import Fernet
from sqlalchemy import select

import app.api.google_drive as google_drive_api
from app.core.token_crypto import PREFIX, decrypt_token, encrypt_token
from app.integrations.catalog import project_integration_catalog
from app.models.google_token import GoogleOAuthToken
from app.models.organization_contract import Organization
from app.models.project import Project


DRIVE = "https://www.googleapis.com/auth/drive"
TASKS = "https://www.googleapis.com/auth/tasks"
CALENDAR = "https://www.googleapis.com/auth/calendar.events"
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"


def _project(db_session) -> Project:
    organization = Organization(name="Synthetic Google acceptance")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    return project


def _status_by_capability(db_session, project_id: int) -> dict[str, object]:
    return {
        item.capability: item
        for item in project_integration_catalog(project_id, db_session)
        if item.provider == "google_workspace"
    }


def test_oauth_callback_encrypts_tokens_and_preserves_refresh_token_on_repeat_consent(
    db_session, monkeypatch,
):
    project = _project(db_session)
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("APP_SECRET_KEY", "synthetic-state-signing-key-32chars")
    monkeypatch.setattr(
        google_drive_api,
        "google_config",
        lambda: ({"web": {}}, "https://workspace.example.test/projects/google/callback"),
    )

    existing = GoogleOAuthToken(
        project_id=project.id,
        access_token=encrypt_token("old-access"),
        refresh_token=encrypt_token("stable-refresh"),
        scopes=" ".join((DRIVE, GMAIL_READ)),
    )
    db_session.add(existing)
    db_session.commit()

    fake_flow = SimpleNamespace(
        oauth2session=SimpleNamespace(scope=list(google_drive_api.SCOPES)),
        credentials=SimpleNamespace(
            token="new-access",
            refresh_token=None,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=[DRIVE, TASKS, CALENDAR, GMAIL_READ, GMAIL_SEND, GMAIL_MODIFY],
        ),
        fetch_token=lambda **_kwargs: {
            "scope": " ".join((DRIVE, TASKS, CALENDAR, GMAIL_READ, GMAIL_SEND, GMAIL_MODIFY)),
        },
    )
    monkeypatch.setattr(
        google_drive_api.Flow,
        "from_client_config",
        lambda *_args, **_kwargs: fake_flow,
    )

    state = google_drive_api._make_oauth_state(project.id)
    response = google_drive_api.google_callback("synthetic-code", state, db_session)

    stored = db_session.scalar(
        select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project.id)
    )
    assert response.status_code == 307
    assert response.headers["location"] == f"/new/?oauth=connected&project_id={project.id}"
    assert stored.access_token.startswith(PREFIX)
    assert stored.refresh_token.startswith(PREFIX)
    assert "new-access" not in stored.access_token
    assert "stable-refresh" not in stored.refresh_token
    assert decrypt_token(stored.access_token) == "new-access"
    assert decrypt_token(stored.refresh_token) == "stable-refresh"
    assert set(stored.scopes.split()) == {DRIVE, TASKS, CALENDAR, GMAIL_READ, GMAIL_SEND, GMAIL_MODIFY}


def test_capability_gating_requires_every_scope_for_each_google_surface(
    db_session, monkeypatch,
):
    project = _project(db_session)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "https://workspace.example.test/projects/google/callback",
    )
    token = GoogleOAuthToken(
        project_id=project.id,
        access_token="synthetic-access-token",
        refresh_token="synthetic-refresh-token",
        scopes=" ".join((DRIVE, TASKS, CALENDAR, GMAIL_READ)),
    )
    db_session.add(token)
    db_session.commit()

    statuses = _status_by_capability(db_session, project.id)
    assert statuses["storage"].connected is True
    assert statuses["task"].connected is True
    assert statuses["calendar"].connected is True
    assert statuses["channel"].connected is False
    assert statuses["channel"].action == "oauth"
    assert statuses["channel"].detail == "authorization required"

    token.scopes += f" {GMAIL_SEND}"
    db_session.commit()

    gmail = _status_by_capability(db_session, project.id)["channel"]
    assert gmail.connected is False
    assert gmail.action == "oauth"

    token.scopes += f" {GMAIL_MODIFY}"
    db_session.commit()

    gmail = _status_by_capability(db_session, project.id)["channel"]
    assert gmail.connected is True
    assert gmail.action == "sync"


def test_capabilities_fail_closed_without_provider_configuration(db_session, monkeypatch):
    project = _project(db_session)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    db_session.add(GoogleOAuthToken(
        project_id=project.id,
        access_token="synthetic-access-token",
        refresh_token="synthetic-refresh-token",
        scopes=" ".join((DRIVE, TASKS, CALENDAR, GMAIL_READ, GMAIL_SEND, GMAIL_MODIFY)),
    ))
    db_session.commit()

    statuses = _status_by_capability(db_session, project.id)
    assert all(item.available is False for item in statuses.values())
    assert all(item.connected is False for item in statuses.values())
    assert all(item.detail == "provider is not configured" for item in statuses.values())


def test_capabilities_fail_closed_for_incomplete_token_or_oauth_config(
    db_session, monkeypatch,
):
    project = _project(db_session)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    token = GoogleOAuthToken(
        project_id=project.id,
        access_token=None,
        refresh_token="synthetic-refresh-token",
        scopes=" ".join((DRIVE, TASKS, CALENDAR, GMAIL_READ, GMAIL_SEND, GMAIL_MODIFY)),
    )
    db_session.add(token)
    db_session.commit()

    statuses = _status_by_capability(db_session, project.id)
    assert all(item.available is False for item in statuses.values())
    assert all(item.connected is False for item in statuses.values())

    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "https://workspace.example.test/projects/google/callback",
    )
    statuses = _status_by_capability(db_session, project.id)
    assert all(item.available is True for item in statuses.values())
    assert all(item.connected is False for item in statuses.values())
