from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.automations.gmail import gmail_project_ids
from app.core.auth import require_user
from app.database import Base, get_db
from app.main import app
from app.models.google_token import GoogleOAuthToken
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.project_service_settings import ProjectServiceSettings
from app.models.user import User
from app.service_settings import gmail_active


def _make_world(role: str = "viewer"):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    org = Organization(name="Service settings org")
    user = User(name="User", email="ss-user@example.test", is_admin=False)
    db.add_all([org, user])
    db.flush()
    project = Project(name="Service settings project", organization_id=org.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    db.commit()
    return engine, db, user, project


def _client(db, user):
    old = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: user
    return old, TestClient(app)


def test_default_settings_all_false():
    from app.service_settings import get_settings

    engine, db, user, project = _make_world()
    try:
        settings = get_settings(db, project.id)
        # Legacy project without a settings row behaves as it did historically:
        # every Google service is enabled by default.
        assert settings.gmail_enabled is True
        assert settings.google_tasks_enabled is True
        assert settings.google_calendar_enabled is True
        assert settings.primary_mail is None
        assert gmail_active(db, project.id) is True
    finally:
        db.close()
        engine.dispose()


def test_put_primary_mail_yandex_mail_disables_gmail_active():
    from app.service_settings import get_settings

    engine, db, user, project = _make_world(role="manager")
    db.add(GoogleOAuthToken(project_id=project.id))
    db.commit()
    old, client = _client(db, user)
    try:
        response = client.put(
            f"/projects/{project.id}/service-settings",
            json={"primary_mail": "yandex_mail"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["primary_mail"] == "yandex_mail"
        assert payload["gmail_enabled"] is True
        assert payload["google_tasks_enabled"] is True
        assert payload["google_calendar_enabled"] is True

        settings = get_settings(db, project.id)
        assert settings.primary_mail == "yandex_mail"
        assert settings.google_tasks_enabled is True
        assert settings.google_calendar_enabled is True
        assert gmail_active(db, project.id) is False
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
        db.close()
        engine.dispose()


def test_put_primary_mail_gmail_forces_gmail_enabled():
    engine, db, user, project = _make_world(role="manager")
    old, client = _client(db, user)
    try:
        response = client.put(
            f"/projects/{project.id}/service-settings",
            json={"primary_mail": "gmail"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["primary_mail"] == "gmail"
        assert payload["gmail_enabled"] is True
        assert gmail_active(db, project.id) is True
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
        db.close()
        engine.dispose()


def test_put_invalid_primary_mail_returns_422():
    engine, db, user, project = _make_world(role="manager")
    old, client = _client(db, user)
    try:
        response = client.put(
            f"/projects/{project.id}/service-settings",
            json={"primary_mail": "outlook"},
        )
        assert response.status_code == 422, response.text
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
        db.close()
        engine.dispose()


def test_viewer_cannot_put():
    engine, db, user, project = _make_world(role="viewer")
    old, client = _client(db, user)
    try:
        response = client.put(
            f"/projects/{project.id}/service-settings",
            json={"gmail_enabled": True},
        )
        assert response.status_code == 403, response.text
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
        db.close()
        engine.dispose()


def test_sync_selection_includes_and_excludes():
    engine, db, user, project = _make_world()
    try:
        db.add(GoogleOAuthToken(project_id=project.id))
        db.add(ProjectServiceSettings(
            project_id=project.id,
            gmail_enabled=False,
            google_tasks_enabled=True,
            google_calendar_enabled=True,
            primary_mail="yandex_mail",
        ))
        db.commit()

        assert project.id not in gmail_project_ids(db)

        row = db.get(ProjectServiceSettings, project.id)
        row.gmail_enabled = True
        row.primary_mail = None
        db.commit()

        assert project.id in gmail_project_ids(db)
    finally:
        db.close()
        engine.dispose()


def test_legacy_project_without_settings_row_is_included():
    engine, db, user, project = _make_world()
    try:
        db.add(GoogleOAuthToken(project_id=project.id))
        db.commit()

        assert gmail_active(db, project.id) is True
        assert project.id in gmail_project_ids(db)
    finally:
        db.close()
        engine.dispose()


def test_created_project_has_disabled_settings_row():
    engine, db, user, project = _make_world(role="editor")
    old, client = _client(db, user)
    try:
        response = client.post(
            "/projects/",
            json={"name": "Brand new project", "organization_id": project.organization_id},
        )
        assert response.status_code == 200, response.text
        new_id = response.json()["id"]

        row = db.get(ProjectServiceSettings, new_id)
        assert row is not None
        assert row.gmail_enabled is False
        assert row.google_tasks_enabled is False
        assert row.google_calendar_enabled is False
        assert row.primary_mail is None
        assert gmail_active(db, new_id) is False

        db.add(GoogleOAuthToken(project_id=new_id))
        db.commit()
        assert new_id not in gmail_project_ids(db)

        response = client.put(
            f"/projects/{new_id}/service-settings",
            json={"primary_mail": "yandex_mail"},
        )
        assert response.status_code == 200, response.text
        assert gmail_active(db, new_id) is False
        assert new_id not in gmail_project_ids(db)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
        db.close()
        engine.dispose()
