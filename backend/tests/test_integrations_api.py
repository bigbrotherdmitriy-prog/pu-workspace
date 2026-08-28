from app.api.integrations import GOOGLE_CAPABILITIES, router


def test_provider_neutral_integration_catalog_route_is_registered():
    assert "/integrations/project" in {route.path for route in router.routes}


def test_google_is_described_as_capabilities_not_core_modules():
    capabilities = {item[0] for item in GOOGLE_CAPABILITIES}
    assert capabilities == {"storage", "task", "calendar", "channel"}


def test_provider_neutral_action_sync_route_is_registered():
    from app.api.tasks import router as tasks_router

    assert "/tasks/sync-actions" in {route.path for route in tasks_router.routes}
