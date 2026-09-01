from sqlalchemy import select

from app.models.drive_connection import DriveConnection
from app.models.google_token import GoogleOAuthToken
from app.models.integration_credential import IntegrationCredential
from app.models.organization_contract import Organization
from app.models.project import Project


def test_project_and_document_core_do_not_branch_on_yandex_provider():
    from pathlib import Path
    root = Path(__file__).parents[1] / "app"
    for path in [root / "core" / "integration_types.py", root / "document_engine.py"]:
        source = path.read_text(encoding="utf-8")
        assert "yandex_disk" not in source


def test_existing_google_project_keeps_legacy_provider_folder_and_token(db_session):
    organization = Organization(name="Owner")
    db_session.add(organization); db_session.flush()
    project = Project(name="Legacy Google", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    connection = DriveConnection(project_id=project.id, provider="google_drive", account_email="old@example.test", root_folder_id="legacy-folder")
    token = GoogleOAuthToken(project_id=project.id, access_token="legacy-encrypted-token")
    db_session.add_all([connection, token]); db_session.commit()

    stored = db_session.scalar(select(DriveConnection).where(DriveConnection.project_id == project.id))
    assert stored.provider == "google_drive"
    assert stored.root_folder_id == "legacy-folder"
    assert db_session.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project.id)).access_token == "legacy-encrypted-token"
    assert db_session.scalar(select(IntegrationCredential).where(IntegrationCredential.project_id == project.id)) is None


def test_yandex_and_google_credentials_are_independent(db_session):
    organization = Organization(name="Owner")
    db_session.add(organization); db_session.flush()
    google = Project(name="Google", organization_id=organization.id); yandex = Project(name="Yandex", organization_id=organization.id)
    db_session.add_all([google, yandex]); db_session.flush()
    db_session.add(GoogleOAuthToken(project_id=google.id, access_token="google-token"))
    db_session.add(IntegrationCredential(project_id=yandex.id, provider="yandex_disk", capability="storage", access_token="yandex-token"))
    db_session.add_all([
        DriveConnection(project_id=google.id, provider="google_drive", account_email="g@example.test", root_folder_id="g-root"),
        DriveConnection(project_id=yandex.id, provider="yandex_disk", account_email="y@example.test", root_folder_id="disk:/Customer/Project", root_display_name="Project", sync_settings='{"recursive":true}'),
    ])
    db_session.commit()

    assert db_session.scalar(select(DriveConnection).where(DriveConnection.project_id == yandex.id)).root_folder_id == "disk:/Customer/Project"
    assert db_session.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == google.id)).access_token == "google-token"
