from app.api.dashboard import router


def test_dashboard_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/dashboard/project" in paths
