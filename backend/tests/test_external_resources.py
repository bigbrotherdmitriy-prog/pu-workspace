from app.models.external_resource import ExternalResourceLink


def test_external_resource_link_is_provider_neutral():
    columns = {column.name for column in ExternalResourceLink.__table__.columns}
    assert {"provider", "resource_type", "external_id", "entity_type", "entity_id"} <= columns
    assert not any(name.startswith("google_") for name in columns)
