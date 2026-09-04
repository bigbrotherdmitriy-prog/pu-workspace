from app.api.workspace import router


def test_processing_queue_and_retry_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/processing-queue" in paths
    assert "/projects/{project_id}/snapshots/{snapshot_id}/retry-build" in paths


def test_processing_routes_are_explicitly_project_scoped():
    methods = {route.path: route.methods for route in router.routes}
    assert "GET" in methods["/projects/{project_id}/processing-queue"]
    assert "POST" in methods["/projects/{project_id}/snapshots/{snapshot_id}/retry-build"]
