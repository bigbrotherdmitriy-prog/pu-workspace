"""Opt-in live Google Drive smoke; uses only a designated test account/folder."""

from datetime import datetime, timezone
import os

import pytest

from app.core.token_crypto import encrypt_token
from app.integrations.google_storage_credentials import google_storage_service
from app.integrations.google_storage_mutation import GoogleDriveConditionalMutationClient
from app.integrations.storage_mutation_live import ExactPreconditionUnavailable
from app.integrations.storage_credentials import (
    DatabaseStorageCredentialPort, StorageCredentialScopeMismatch,
)
from app.models.integration_credential import IntegrationCredential
from app.models.organization_contract import Organization
from app.models.project import Project


@pytest.mark.skipif(os.getenv("PU_MVP1_GOOGLE_LIVE_TEST") != "1", reason="live Google test account not configured")
def test_live_google_copy_rename_includes_tenant_and_project_ownership(db_session):
    required = {
        name: os.getenv(name, "") for name in (
            "MVP1_GOOGLE_LIVE_ACCESS_TOKEN", "MVP1_GOOGLE_LIVE_REFRESH_TOKEN",
            "MVP1_GOOGLE_LIVE_SOURCE_FILE_ID", "MVP1_GOOGLE_LIVE_FOLDER_ID",
            "GOOGLE_STORAGE_CLIENT_ID", "GOOGLE_STORAGE_CLIENT_SECRET",
            "TOKEN_ENCRYPTION_KEY",
        )
    }
    assert all(required.values()), "dedicated live-test values are required"
    tenant = Organization(name="Synthetic live tenant")
    foreign_tenant = Organization(name="Synthetic foreign tenant")
    db_session.add_all([tenant, foreign_tenant]); db_session.flush()
    project = Project(name="Synthetic live project", organization_id=tenant.id)
    foreign_project = Project(name="Synthetic foreign project", organization_id=foreign_tenant.id)
    db_session.add_all([project, foreign_project]); db_session.flush()
    credential = IntegrationCredential(
        project_id=project.id, provider="google_drive", capability="storage",
        access_token=encrypt_token(required["MVP1_GOOGLE_LIVE_ACCESS_TOKEN"]),
        refresh_token=encrypt_token(required["MVP1_GOOGLE_LIVE_REFRESH_TOKEN"]),
        token_uri="https://oauth2.googleapis.com/token", expires_at=datetime.now(timezone.utc),
        scopes="openid email https://www.googleapis.com/auth/drive",
        account_external_id="live-test-account",
    )
    db_session.add(credential); db_session.flush()
    connection_id = f"storage-credential:{credential.id}"
    port = DatabaseStorageCredentialPort()

    # These ownership denials are part of the same live-provider scenario and
    # happen before service construction/provider I/O.
    with pytest.raises(StorageCredentialScopeMismatch):
        port.resolve(db_session, connection_id=connection_id, project_id=foreign_project.id,
                     organization_id=foreign_tenant.id, provider="google_drive")
    with pytest.raises(StorageCredentialScopeMismatch):
        port.resolve(db_session, connection_id=connection_id, project_id=project.id,
                     organization_id=foreign_tenant.id, provider="google_drive")

    resolved = port.resolve(db_session, connection_id=connection_id, project_id=project.id,
                            organization_id=tenant.id, provider="google_drive")
    service = google_storage_service(resolved, db_session)
    source_id = required["MVP1_GOOGLE_LIVE_SOURCE_FILE_ID"]
    folder_id = required["MVP1_GOOGLE_LIVE_FOLDER_ID"]
    copy = service.files().copy(
        fileId=source_id, body={"name": "PU MVP1 OAuth smoke copy", "parents": [folder_id]},
        fields="id,name,parents", supportsAllDrives=True,
    ).execute()
    copy_id = copy["id"]
    try:
        metadata = service.files().get(
            fileId=copy_id, fields="id,name,parents", supportsAllDrives=True,
        ).execute()
        assert metadata["id"] == copy_id and folder_id in metadata.get("parents", [])
        client = GoogleDriveConditionalMutationClient(service)
        original = client.get_exact_state(copy_id)
        renamed = client.rename_if_revision(
            copy_id, "PU MVP1 OAuth smoke renamed",
            expected_revision=original.revision, operation_key="live-smoke-rename",
        )
        assert renamed.name == "PU MVP1 OAuth smoke renamed"

        # Simulate a concurrent actor on the designated copy only.  The stale
        # revision must fail before our second mutation, then the compensating
        # operation uses the newly observed exact revision.
        service.files().update(
            fileId=copy_id, body={"name": "PU MVP1 OAuth smoke concurrent"},
            fields="id,name", supportsAllDrives=True,
        ).execute()
        with pytest.raises(ExactPreconditionUnavailable, match="conflict_source_changed"):
            client.rename_if_revision(
                copy_id, "must not overwrite", expected_revision=renamed.revision,
                operation_key="live-smoke-stale",
            )
        concurrent = client.get_exact_state(copy_id)
        rolled_back = client.rename_if_revision(
            copy_id, original.name, expected_revision=concurrent.revision,
            operation_key="live-smoke-compensate",
        )
        assert rolled_back.name == original.name
    finally:
        service.files().update(
            fileId=copy_id, body={"trashed": True}, fields="id", supportsAllDrives=True,
        ).execute()
