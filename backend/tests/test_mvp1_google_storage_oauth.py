from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event, Lock
from time import sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register the complete schema for SQLite contract tests
from app.database import Base
from app.api import mvp1_google_oauth as oauth
from app.api import google_drive as legacy_oauth
from app.api.drive import DriveConnectRequest, connect_drive
from app.integrations import storage
from app.integrations.storage_credentials import (
    DatabaseStorageCredentialPort, StorageCredentialScopeMismatch, connection_id_for,
)
from app.models.drive_connection import DriveConnection
from app.models.audit_log import AuditLog
from app.models.integration_credential import IntegrationCredential
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import MailboxCredentialGeneration
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.storage_oauth_state import StorageOAuthState
from app.models.user import User


def _postgres_url() -> str | None:
    if os.getenv("PU_MVP1_OAUTH_POSTGRES") != "1":
        return None
    raw = (os.getenv("DATABASE_URL") or "").strip()
    if not raw:
        pytest.fail("DATABASE_URL is required for PU_MVP1_OAUTH_POSTGRES=1")
    url = make_url(raw.replace("postgresql://", "postgresql+psycopg://", 1))
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        pytest.fail("Refusing to run OAuth checks against a non-test PostgreSQL database")
    return url.render_as_string(hide_password=False)


@pytest.fixture
def oauth_db_session():
    postgres_url = _postgres_url()
    if postgres_url is None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
    else:
        engine = create_engine(postgres_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


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


def test_storage_credential_port_resolves_exact_connection_and_scope(oauth_db_session):
    db_session = oauth_db_session
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


def test_drive_binding_uses_verified_credential_account(oauth_db_session):
    db_session = oauth_db_session
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


def test_storage_adapter_uses_connection_id_without_project_fallback(oauth_db_session, monkeypatch):
    db_session = oauth_db_session
    world = _world(db_session)
    selected = _credential(db_session, world)
    connection = DriveConnection(
        project_id=world.project.id, provider="google_drive", account_email="verified@example.test",
        root_folder_id="root", connection_id=connection_id_for(selected.id), status="connected",
    )
    db_session.add(connection); db_session.flush()
    calls = []
    monkeypatch.setattr(storage, "google_storage_service", lambda resolved, db: calls.append(resolved.credential_id) or object())

    storage.storage_for_project(world.project.id, db_session)
    assert calls == [selected.id]

    connection.connection_id = connection_id_for(selected.id + 1000)
    with pytest.raises(HTTPException) as exc:
        storage.storage_for_project(world.project.id, db_session)
    assert exc.value.status_code == 409
    assert calls == [selected.id]


def test_oauth_start_persists_user_project_tenant_bound_single_use_state(oauth_db_session, monkeypatch):
    db_session = oauth_db_session
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
    row = db_session.scalar(select(StorageOAuthState).where(
        StorageOAuthState.state_hash == oauth._state_hash(captured["state"]),
    ))
    assert response["authorization_url"] == "https://accounts.example.test/oauth"
    assert row.project_id == world.project.id and row.organization_id == world.org.id
    assert row.user_id == world.user.id and row.state_hash == oauth._state_hash(captured["state"])
    assert row.provider == "google_drive"
    assert row.state_hash != captured["state"]
    assert set(captured["scopes"]) == set(oauth.GOOGLE_STORAGE_SCOPES)


def test_verified_account_accepts_only_verified_google_identity(monkeypatch):
    credentials = SimpleNamespace(id_token="synthetic-signed-id-token")
    monkeypatch.setattr(
        oauth.google_id_token,
        "verify_oauth2_token",
        lambda token, request, audience: {
            "sub": "stable-google-subject",
            "email": "verified@example.test",
            "email_verified": True,
        },
    )

    assert oauth._verified_account(credentials, "synthetic-client") == (
        "stable-google-subject",
        "verified@example.test",
    )


@pytest.mark.parametrize(
    "claims",
    [
        {"email": "verified@example.test", "email_verified": True},
        {"sub": "stable-google-subject", "email": "unverified@example.test", "email_verified": False},
    ],
)
def test_verified_account_rejects_missing_subject_or_unverified_email(monkeypatch, claims):
    credentials = SimpleNamespace(id_token="synthetic-signed-id-token")
    monkeypatch.setattr(
        oauth.google_id_token,
        "verify_oauth2_token",
        lambda token, request, audience: claims,
    )

    with pytest.raises(HTTPException) as exc:
        oauth._verified_account(credentials, "synthetic-client")

    assert exc.value.status_code == 401


def test_oauth_exchange_accepts_google_canonical_email_scope():
    fake_credentials = SimpleNamespace(scopes=None)

    class FakeFlow:
        oauth2session = SimpleNamespace(scope="requested")
        credentials = fake_credentials

        def fetch_token(self, *, code):
            assert code == "one-time-code"
            return {
                "scope": " ".join((
                    "openid",
                    "https://www.googleapis.com/auth/userinfo.email",
                    "https://www.googleapis.com/auth/drive",
                )),
            }

    assert oauth._fetch_credentials(FakeFlow(), "one-time-code") is fake_credentials


@pytest.mark.parametrize(
    "granted",
    [
        "openid email",
        "openid https://www.googleapis.com/auth/drive",
        "email https://www.googleapis.com/auth/drive",
    ],
)
def test_oauth_exchange_rejects_every_required_scope_subset(granted):
    class FakeFlow:
        oauth2session = SimpleNamespace(scope="requested")
        credentials = SimpleNamespace(scopes=None)

        def fetch_token(self, *, code):
            return {"scope": granted}

    with pytest.raises(HTTPException) as exc:
        oauth._fetch_credentials(FakeFlow(), "one-time-code")
    assert exc.value.status_code == 400


def test_oauth_exchange_accepts_scope_superset():
    class FakeFlow:
        oauth2session = SimpleNamespace(scope="requested")
        credentials = SimpleNamespace(scopes=None)

        def fetch_token(self, *, code):
            return {"scope": " ".join((*oauth.GOOGLE_STORAGE_SCOPES, "synthetic.extra"))}

    assert oauth._fetch_credentials(FakeFlow(), "one-time-code") is FakeFlow.credentials


def test_oauth_callback_consumes_state_and_stores_exact_storage_credential(oauth_db_session, monkeypatch):
    db_session = oauth_db_session
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
    credential = db_session.scalar(select(IntegrationCredential).where(
        IntegrationCredential.project_id == world.project.id,
        IntegrationCredential.provider == "google_drive",
        IntegrationCredential.capability == "storage",
    ))
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
    assert db_session.scalar(select(GoogleOAuthToken)) is None
    assert db_session.scalar(select(MailboxCredentialGeneration)) is None

    with pytest.raises(HTTPException) as replay:
        oauth.google_storage_callback("another-code", raw_state, db_session, world.user)
    assert replay.value.status_code == 400


def test_oauth_callback_rejects_different_user_before_exchange(oauth_db_session, monkeypatch):
    db_session = oauth_db_session
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


def test_oauth_callback_rejects_expired_state_before_exchange(oauth_db_session, monkeypatch):
    db_session = oauth_db_session
    world = _world(db_session)
    now = datetime.now(timezone.utc)
    raw_state = "expired-state"
    db_session.add(StorageOAuthState(
        state_hash=oauth._state_hash(raw_state), organization_id=world.org.id,
        project_id=world.project.id, user_id=world.user.id, provider="google_drive",
        created_at=now - oauth.STATE_TTL * 2, expires_at=now - oauth.STATE_TTL,
    ))
    db_session.commit()
    monkeypatch.setattr(oauth, "_fetch_credentials", lambda *_: pytest.fail("provider exchange must not run"))

    with pytest.raises(HTTPException) as exc:
        oauth.google_storage_callback("code", raw_state, db_session, world.user)

    assert exc.value.status_code == 400


def test_oauth_callback_rejects_state_for_another_provider_before_exchange(
    oauth_db_session, monkeypatch,
):
    db_session = oauth_db_session
    world = _world(db_session)
    now = datetime.now(timezone.utc)
    raw_state = "state-for-another-provider"
    db_session.add(StorageOAuthState(
        state_hash=oauth._state_hash(raw_state), organization_id=world.org.id,
        project_id=world.project.id, user_id=world.user.id, provider="yandex_disk",
        created_at=now, expires_at=now + oauth.STATE_TTL,
    ))
    db_session.commit()
    monkeypatch.setattr(oauth, "_fetch_credentials", lambda *_: pytest.fail("provider exchange must not run"))

    with pytest.raises(HTTPException) as exc:
        oauth.google_storage_callback("code", raw_state, db_session, world.user)

    assert exc.value.status_code == 400


def test_oauth_callback_rechecks_role_before_exchange(oauth_db_session, monkeypatch):
    db_session = oauth_db_session
    world = _world(db_session)
    now = datetime.now(timezone.utc)
    raw_state = "revoked-role-state"
    db_session.add(StorageOAuthState(
        state_hash=oauth._state_hash(raw_state), organization_id=world.org.id,
        project_id=world.project.id, user_id=world.user.id, provider="google_drive",
        created_at=now, expires_at=now + oauth.STATE_TTL,
    ))
    db_session.execute(delete(ProjectMember).where(
        ProjectMember.project_id == world.project.id,
        ProjectMember.user_id == world.user.id,
    ))
    db_session.commit()
    monkeypatch.setattr(oauth, "_fetch_credentials", lambda *_: pytest.fail("provider exchange must not run"))

    with pytest.raises(HTTPException) as exc:
        oauth.google_storage_callback("code", raw_state, db_session, world.user)

    assert exc.value.status_code == 403


def test_oauth_reauthorization_preserves_selected_nested_folder(oauth_db_session):
    db_session = oauth_db_session
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


def test_storage_reauthorization_does_not_mutate_legacy_google_token(oauth_db_session):
    db_session = oauth_db_session
    world = _world(db_session)
    legacy = GoogleOAuthToken(
        project_id=world.project.id,
        access_token="legacy-access",
        refresh_token="legacy-refresh",
        token_uri="https://oauth2.googleapis.com/token",
        scopes="legacy-scope",
    )
    db_session.add(legacy)
    db_session.flush()

    oauth._bind_credential(
        db_session,
        project=world.project,
        connection_id="storage-credential:99",
        account_email="verified@example.test",
    )

    db_session.refresh(legacy)
    assert (legacy.access_token, legacy.refresh_token, legacy.scopes) == (
        "legacy-access", "legacy-refresh", "legacy-scope",
    )


def test_legacy_reconnect_preserves_storage_credential_and_creates_mailbox_generation(
    oauth_db_session, monkeypatch,
):
    db_session = oauth_db_session
    world = _world(db_session)
    storage_credential = _credential(db_session, world)
    original_storage_values = (
        storage_credential.access_token,
        storage_credential.refresh_token,
        storage_credential.scopes,
        storage_credential.account_external_id,
    )
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("APP_SECRET_KEY", "synthetic-state-signing-key-32chars")
    monkeypatch.setattr(
        legacy_oauth,
        "google_config",
        lambda: ({"web": {"client_id": "synthetic-client"}}, "https://app.example.test/callback"),
    )
    monkeypatch.setattr(legacy_oauth, "verified_google_subject", lambda *_: "legacy-subject")
    credentials = SimpleNamespace(
        token="legacy-new-access",
        refresh_token="legacy-new-refresh",
        token_uri="https://oauth2.googleapis.com/token",
        id_token="synthetic-id-token",
        scopes=legacy_oauth.SCOPES,
    )
    fake_flow = SimpleNamespace(
        oauth2session=SimpleNamespace(scope=list(legacy_oauth.SCOPES)),
        credentials=credentials,
        fetch_token=lambda **_: {"scope": " ".join(legacy_oauth.SCOPES)},
    )
    monkeypatch.setattr(
        legacy_oauth.Flow,
        "from_client_config",
        lambda *_args, **_kwargs: fake_flow,
    )

    response = legacy_oauth.google_callback(
        "synthetic-code",
        legacy_oauth._make_oauth_state(world.project.id),
        db_session,
    )

    db_session.refresh(storage_credential)
    assert response.status_code == 307
    assert (
        storage_credential.access_token,
        storage_credential.refresh_token,
        storage_credential.scopes,
        storage_credential.account_external_id,
    ) == original_storage_values
    legacy_token = db_session.scalar(select(GoogleOAuthToken).where(
        GoogleOAuthToken.project_id == world.project.id,
    ))
    assert legacy_token is not None
    assert db_session.scalar(select(func.count()).select_from(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.google_token_id == legacy_token.id,
    )) == 1


def test_legacy_and_mvp1_storage_oauth_routes_coexist_without_collision():
    from app.main import app

    routes = {
        (route.path, tuple(sorted(getattr(route, "methods", ()) or ())))
        for route in app.routes
    }

    assert ("/projects/{project_id}/google/auth", ("GET",)) in routes
    assert ("/projects/google/callback", ("GET",)) in routes
    assert ("/projects/{project_id}/storage/google/oauth", ("GET",)) in routes
    assert ("/projects/storage/google/callback", ("GET",)) in routes


def test_postgres_concurrent_callbacks_consume_state_exactly_once(monkeypatch):
    postgres_url = _postgres_url()
    if postgres_url is None:
        pytest.skip("real PostgreSQL OAuth concurrency requires PU_MVP1_OAUTH_POSTGRES=1")

    engine = create_engine(postgres_url, pool_size=4, max_overflow=0)
    suffix = uuid4().hex
    raw_state = f"concurrent-state-{suffix}"
    now = datetime.now(timezone.utc)
    with Session(engine) as setup:
        org = Organization(name=f"OAuth concurrency tenant {suffix}")
        user = User(name="OAuth concurrency manager", email=f"oauth-{suffix}@example.test")
        setup.add_all([org, user])
        setup.flush()
        project = Project(name=f"OAuth concurrency project {suffix}", organization_id=org.id)
        setup.add(project)
        setup.flush()
        setup.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        setup.add(StorageOAuthState(
            state_hash=oauth._state_hash(raw_state),
            organization_id=org.id,
            project_id=project.id,
            user_id=user.id,
            provider="google_drive",
            created_at=now,
            expires_at=now + oauth.STATE_TTL,
        ))
        setup.commit()
        project_id, user_id = project.id, user.id

    credentials = SimpleNamespace(
        token="synthetic-access",
        refresh_token="synthetic-refresh",
        token_uri="https://oauth2.googleapis.com/token",
        scopes=oauth.GOOGLE_STORAGE_SCOPES,
        expiry=None,
        id_token="synthetic-id-token",
    )
    first_inside_exchange = Event()
    release_first = Event()
    exchange_lock = Lock()
    exchange_calls = 0

    class FakeFlow:
        @classmethod
        def from_client_config(cls, *args, **kwargs):
            return cls()

    def controlled_exchange(flow, code):
        nonlocal exchange_calls
        with exchange_lock:
            exchange_calls += 1
            call_number = exchange_calls
        if call_number == 1:
            first_inside_exchange.set()
            assert release_first.wait(timeout=10), "controlled first callback was not released"
        return credentials

    monkeypatch.setattr(
        oauth,
        "_config",
        lambda: ({"web": {"client_id": "synthetic-client"}}, "https://app.example.test/callback"),
    )
    monkeypatch.setattr(oauth, "Flow", FakeFlow)
    monkeypatch.setattr(oauth, "_fetch_credentials", controlled_exchange)
    monkeypatch.setattr(
        oauth,
        "_verified_account",
        lambda value, client_id: (f"subject-{suffix}", f"verified-{suffix}@example.test"),
    )
    monkeypatch.setattr(oauth, "encrypt_token", lambda value: f"encrypted:{value}" if value else None)

    def callback(code):
        with Session(engine) as db:
            thread_user = db.get(User, user_id)
            try:
                return oauth.google_storage_callback(code, raw_state, db, thread_user).status_code
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(callback, "first-code")
        assert first_inside_exchange.wait(timeout=10), "first callback did not acquire the state lock"
        second = executor.submit(callback, "second-code")
        sleep(0.25)
        assert not second.done(), "second callback did not block on the PostgreSQL row lock"
        assert exchange_calls == 1
        release_first.set()
        statuses = sorted((first.result(timeout=10), second.result(timeout=10)))

    assert statuses == [307, 400]
    assert exchange_calls == 1
    with Session(engine) as verify:
        state_row = verify.get(StorageOAuthState, oauth._state_hash(raw_state))
        assert state_row is not None and state_row.consumed_at is not None
        assert verify.scalar(select(func.count()).select_from(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.provider == "google_drive",
            IntegrationCredential.capability == "storage",
        )) == 1
        assert verify.scalar(select(func.count()).select_from(DriveConnection).where(
            DriveConnection.project_id == project_id,
        )) == 1
        assert verify.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "mvp1_storage_oauth_connected",
            AuditLog.entity_type == "project",
            AuditLog.entity_id == project_id,
        )) == 1
    engine.dispose()
