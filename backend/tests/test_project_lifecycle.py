from app.api.projects import router


def test_project_lifecycle_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}" in paths
    assert "/projects/{project_id}/restore" in paths
    assert "/projects/{project_id}/safe-copies" in paths
    assert "/projects/{project_id}/safe-copies/trash" in paths
