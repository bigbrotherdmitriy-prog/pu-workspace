from app.models.external_resource import ExternalResourceLink
from app.integrations.external_resources import external_id_for
from types import SimpleNamespace
from unittest.mock import Mock


def test_external_resource_link_is_provider_neutral():
    columns = {column.name for column in ExternalResourceLink.__table__.columns}
    assert {"provider", "resource_type", "external_id", "entity_type", "entity_id"} <= columns
    assert not any(name.startswith("google_") for name in columns)


def test_external_id_prefers_provider_neutral_link():
    db = Mock()
    db.scalar.return_value = SimpleNamespace(external_id="generic-123", sync_status="synced")
    assert external_id_for(
        db, entity_type="task", entity_id=7, provider="google_workspace",
        resource_type="task", legacy_id="legacy-456",
    ) == "generic-123"


def test_external_id_uses_legacy_fallback_for_deleted_link():
    db = Mock()
    db.scalar.return_value = SimpleNamespace(external_id="deleted-123", sync_status="deleted")
    assert external_id_for(
        db, entity_type="task", entity_id=7, provider="google_workspace",
        resource_type="task", legacy_id="legacy-456",
    ) == "legacy-456"
