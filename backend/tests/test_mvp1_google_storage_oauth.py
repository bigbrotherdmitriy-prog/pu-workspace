from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import mvp1_google_oauth as oauth
from app.api.drive import DriveConnectRequest, connect_drive
from app.integrations import storage
from app.integrations.storage_credentials import (
    DatabaseStorageCredentialPort, StorageCredentialScopeMismatch, connection_id_for,
)
from app.models.drive_connection import DriveConnection
from app.models.integration_credential import IntegrationCredential
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.storage_oauth_state import StorageOAuthState
from app.models.user import User


def _world(db):
    org = Organization(name="Synthetic tenant")
    other_org = Organization(name="Other synthetic tenant")
    user = User(name="Synthetic manager", email="manager@example.test")
    db.add_all([org, other_org, user]); db.flush()
    project = Project(name="Synthetic project", organization_id=org.id)
    other_project = Project(name="Other project", organization_id=other_org.id)
    db.add_all([project, other_project]); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager")); db.flush()
    return SimpleNamespace(org=org, other_org=other_org, user=user,
                           project=project, other_project=other_project)


def _credential(db, world, *, project=None):
    project = project or world.project
    row = IntegrationCredential(
        project_id=project.id, provider="google_drive", capability="storage",
        access_token="encrypted-access", refresh_token="encrypted-refresh",
        token_uri="https://oauth2.googleapis.com/token", scopes="openid email https://www.googleapis.com/auth/drive",
        account_external_id="google-subject", account_email="verified@example.test",
    )
    db.add(row); db.flush()
    return row


def test_storage_credential_port_resolves_exact_connection_and_scope(db_session):
    world = _world(db_session)
    credential = _credential(db_session, world)
    port = DatabaseStorageCredentialPort()

    resolved = port.resolve(
        db_session, connection_id=connection_id_for(credential.id),
        project_id=world.project.id, organization_id=world.org.id, provider="google_drive",
    )

    assert resolved.credential_id == credential.id
    assert resolved.account_email == "verified@example.test"
    with pytest.raises(StorageCredentialScopeMismatch):
        port.resolve(
            db_session, connection_id=connection_id_for(credential.id),
            project_id=world.other_project.id, organization_id=world.other_org.id,
            provider="google_drive",
        )
    with pytest.raises(StorageCredentialScopeMismatch):
        port.resolve(
            db_session, connection_id=connection_id_for(credential.id),
            project_id=world.project.id, organization_id=world.other_org.id,
            provider="google_drive",
        )


def test_drive_binding_uses_verified_credential_account(db_session):
    world = _world(db_session)
    credential = _credential(db_session, world)
    result = connect_drive(
        world.project.id,
        DriveConnectRequest(
            account_email="spoofed@example.test", root_folder_id="opaqueFolder",
            provider="google_drive", connection_id=connection_id_for(credential.id),
        ),
        db_session, world.user,
    )
    assert result["connection_id"] == connection_id_for(credential.id)
    assert result["account_email"] == "verified@example.test"


def test_storage_adapter_uses_connection_id_without_project_fallback(db_session, monkeypatch):
    world = _world(db_session)
    selected = _credential(db_session, world)
    db_session.add(DriveConnection(
        project_id=world.project.id, provider="google_drive", account_email="verified@example.test",
        root_folder_id="root", connection_id=connection_id_for(selected.id), status="connected",
    )); db_session.flush()
    calls = []
    monkeypatch.setattr(storage, "google_storage_service", lambda resolved, db: calls.append(resolved.credential_id) or object())

    storage.storage_for_project(world.project.id, db_session)
    assert calls == [selected.id]

    db_session.get(DriveConnection, 1).connection_id = connection_id_for(selected.id + 1000)
    with pytest.raises(HTTPException) as exc:
        storage.storage_for_project(world.project.id, db_session)
    assert exc.value.status_code == 409
    assert calls == [selected.id]


def test_oauth_start_persists_user_project_tenant_bound_single_use_state(db_session, monkeypatch):
    world = _world(db_session)
    captured = {}

    class FakeFlow:
        @classmethod
        def from_client_config(cls, config, *, scopes, redirect_uri):
            captured.update(scopes=scopes, redirect_uri=redirect_uri)
            return cls()

        def authorization_url(self, **kwargs):
            captured["state"] = kwargs["state"]
            return "https://accounts.example.test/oauth", kwargs["state"]

    monkeypatch.setattr(oauth, "_config", lambda: ({"web": {}}, "https://app.example.test/callback"))
    monkeypatch.setattr(oauth, "Flow", FakeFlow)
    response = oauth.google_storage_auth(world.project.id, db_session, world.user)
    row = db_session.scalar(select(StorageOAuthState))
    assert response["authorization_url"] == "https://accounts.example.test/oauth"
    assert row.project_id == world.project.id and row.organization_id == world.org.id
    assert row.user_id == world.user.id and row.state_hash == oauth._state_hash(captured["state"])
    assert set(captured["scopes"]) == set(oauth.GOOGLE_STORAGE_SCOPES)


def test_oauth_callback_consumes_state_and_stores_exact_storage_credential(db_session, monkeypatch):
    world = _world(db_session)
    now = datetime.now(timezone.utc)
    raw_state = "synthetic-state-with-enough-entropy"
    db_session.add(StorageOAuthState(
        state_hash=oauth._state_hash(raw_state), organization_id=world.org.id,
        project_id=world.project.id, user_id=world.user.id, provider="google_drive",
        created_at=now, expires_at=now + oauth.STATE_TTL,
    )); db_session.commit()
    credentials = SimpleNamespace(
        token="access", refresh_token="refresh", token_uri="https://oauth2.googleapis.com/token",
        scopes=oauth.GOOGLE_STORAGE_SCOPES, expiry=None, id_token="signed-id-token",
    )

    class FakeFlow:
        @classmethod
        def from_client_config(cls, *args, **kwargs):
            return cls()

    monkeypatch.setattr(oauth, "_config", lambda: ({"web": {"client_id": "client"}}, "https://app.example.test/callback"))
    monkeypatch.setattr(oauth, "Flow", FakeFlow)
    monkeypatch.setattr(oauth, "_fetch_credentials", lambda flow, code: credentials)
    monkeypatch.setattr(oauth, "_verified_account", lambda value, client_id: ("subject", "verified@example.test"))
    monkeypatch.setattr(oauth, "encrypt_token", lambda value: f"encrypted:{value}" if value else None)

    response = oauth.google_storage_callback("one-time-code", raw_state, db_session, world.user)
    credential = db_session.scalar(select(IntegrationCredential))
    assert response.status_code == 307
    assert f"connection_id={connection_id_for(credential.id)}" in response.headers["location"]
    assert credential.project_id == world.project.id and credential.provider == "google_drive"
    assert credential.account_external_id == "subject"
    connection = db_session.scalar(select(DriveConnection).where(
        DriveConnection.project_id == world.project.id,
    ))
    assert connection.provider == "google_drive"
    assert connection.root_folder_id == "root"
    assert connection.account_email == "verified@example.test"
    assert connection.connection_id == connection_id_for(credential.id)

    with pytest.raises(HTTPException) as replay:
        oauth.google_storage_callback("another-code", raw_state, db_session, world.user)
    assert replay.value.status_code == 400


def test_oauth_callback_rejects_different_user_before_exchange(db_session, monkeypatch):
    world = _world(db_session)
    other = User(name="Other", email="other@example.test")
    db_session.add(other); db_session.flush()
    now = datetime.now(timezone.utc)
    raw_state = "state-owned-by-manager"
    db_session.add(StorageOAuthState(
        state_hash=oauth._state_hash(raw_state), organization_id=world.org.id,
        project_id=world.project.id, user_id=world.user.id, provider="google_drive",
        created_at=now, expires_at=now + oauth.STATE_TTL,
    )); db_session.commit()
    monkeypatch.setattr(oauth, "_fetch_credentials", lambda *_: pytest.fail("provider exchange must not run"))
    with pytest.raises(HTTPException) as exc:
        oauth.google_storage_callback("code", raw_state, db_session, other)
    assert exc.value.status_code == 400


def test_oauth_reauthorization_preserves_selected_nested_folder(db_session):
    world = _world(db_session)
    connection = DriveConnection(
        project_id=world.project.id,
        provider="google_drive",
        account_email="old@example.test",
        root_folder_id="nested-folder-id",
        root_display_name="Customer / Project",
        connection_id="storage-credential:41",
        status="configured",
    )
    db_session.add(connection)
    db_session.flush()

    oauth._bind_credential(
        db_session,
        project=world.project,
        connection_id="storage-credential:42",
        account_email="new@example.test",
    )

    assert connection.connection_id == "storage-credential:42"
    assert connection.account_email == "new@example.test"
    assert connection.root_folder_id == "nested-folder-id"
    assert connection.root_display_name == "Customer / Project"
