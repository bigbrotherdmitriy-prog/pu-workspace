from app.api.integrations import GOOGLE_CAPABILITIES, router
from app.integrations.catalog import IntegrationStatus


def test_provider_neutral_integration_catalog_route_is_registered():
    assert "/integrations/project" in {route.path for route in router.routes}


def test_google_is_described_as_capabilities_not_core_modules():
    capabilities = {item[0] for item in GOOGLE_CAPABILITIES}
    assert capabilities == {"storage", "task", "calendar", "channel"}


def test_provider_neutral_action_sync_route_is_registered():
    from app.api.tasks import router as tasks_router

    assert "/tasks/sync-actions" in {route.path for route in tasks_router.routes}


def test_external_action_approval_accepts_neutral_and_legacy_fields():
    from app.api.tasks import ExternalActionApproval

    neutral = ExternalActionApproval.model_validate({
        "publish_task": False,
        "publish_calendar": True,
    })
    assert neutral.publish_task is False
    assert neutral.publish_calendar is True

    legacy = ExternalActionApproval.model_validate({
        "create_google_task": True,
        "create_calendar_event": False,
    })
    assert legacy.publish_task is True
    assert legacy.publish_calendar is False


def test_catalog_status_serializes_provider_neutral_fields():
    status = IntegrationStatus(
        key="demo:channel",
        provider="demo",
        capability="channel",
        name="Demo",
        description="Test adapter",
        available=True,
        connected=False,
        detail="authorization required",
    ).as_dict()

    assert status["provider"] == "demo"
    assert status["capability"] == "channel"
    assert status["connected"] is False
    assert "google" not in status["key"]
