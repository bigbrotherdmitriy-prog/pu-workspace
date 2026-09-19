import base64
from datetime import datetime, timezone

import pytest

from app.core.v54_interfaces import RequestScope
from app.core.v54_permissions import SourceEvidenceError
from app.local_upload_staging import (
    UploadScope,
    configure_local_upload_runtime,
    get_local_upload_runtime,
    local_upload_runtime_ready,
)
from app.integrations import catalog
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.staging import production_local_upload


def test_production_runtime_is_off_by_default(monkeypatch):
    monkeypatch.delenv("PU_LOCAL_UPLOAD_RUNTIME", raising=False)
    assert production_local_upload.install_production_local_upload_runtime() is False


@pytest.mark.parametrize("database_url", [
    "sqlite:///pu_workspace.db",
    "postgresql://pu_test:secret@db:5432/pu_test",
])
def test_production_runtime_rejects_non_production_database(monkeypatch, database_url):
    monkeypatch.setenv("PU_LOCAL_UPLOAD_RUNTIME", "production")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PU_LOCAL_UPLOAD_STAGING_ROOT", str(production_local_upload._ROOT))
    with pytest.raises(
        production_local_upload.ProductionLocalUploadConfigurationError,
        match="unsafe_production_local_upload_configuration",
    ):
        production_local_upload.install_production_local_upload_runtime()


def test_production_runtime_installs_shared_encrypted_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(production_local_upload, "_ROOT", tmp_path)
    monkeypatch.setenv("PU_LOCAL_UPLOAD_RUNTIME", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://pu_user:secret@db:5432/pu_workspace")
    monkeypatch.setenv("PU_LOCAL_UPLOAD_STAGING_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"p" * 32).decode("ascii"),
    )
    try:
        assert production_local_upload.install_production_local_upload_runtime() is True
        assert local_upload_runtime_ready() is True
        assert {"application/pdf", "image/jpeg", "image/png"} <= (
            get_local_upload_runtime().allowed_mime_types
        )
    finally:
        configure_local_upload_runtime(None)


def _scope(organization_id: int, project_id: int, user_id: int) -> RequestScope:
    tenant = {"kind": "int", "value": str(organization_id)}
    return RequestScope(
        actor={
            "namespace": "pu", "type": "user", "tenant_id": tenant,
            "id": {"kind": "int", "value": str(user_id)},
        },
        tenant=tenant,
        project={
            "namespace": "pu", "type": "project", "tenant_id": tenant,
            "id": {"kind": "int", "value": str(project_id)},
        },
        correlation_id="dfe88b4c-0044-424c-a6e5-9880c819a2de",
    )


def test_production_policy_rechecks_live_manager_membership(db_session, user_factory):
    organization = Organization(name="Production upload tenant")
    manager = user_factory()
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Production upload project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    membership = ProjectMember(project_id=project.id, user_id=manager.id, role="manager")
    db_session.add(membership)
    db_session.flush()

    authority = production_local_upload._authority(
        db_session, UploadScope(manager.id, project.id),
    )
    scope = _scope(organization.id, project.id, manager.id)
    assert authority.policy.require(
        db_session, scope, "write", datetime.now(timezone.utc), lock=True,
    )

    membership.role = "viewer"
    db_session.flush()
    with pytest.raises(SourceEvidenceError, match="resource_unavailable"):
        authority.policy.require(
            db_session, scope, "fragment", datetime.now(timezone.utc), lock=True,
        )


def test_integration_catalog_reports_local_upload_runtime_truthfully(
    db_session, monkeypatch,
):
    organization = Organization(name="Catalog tenant")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Catalog project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()

    monkeypatch.setattr(catalog, "local_upload_runtime_ready", lambda: False)
    unavailable = next(
        item for item in catalog.project_integration_catalog(project.id, db_session)
        if item.key == "local:storage"
    )
    assert unavailable.available is False
    assert unavailable.connected is False
    assert unavailable.detail == "secure upload runtime is not configured"

    monkeypatch.setattr(catalog, "local_upload_runtime_ready", lambda: True)
    ready = next(
        item for item in catalog.project_integration_catalog(project.id, db_session)
        if item.key == "local:storage"
    )
    assert ready.available is True
    assert ready.connected is True
    assert ready.detail == "ready"
