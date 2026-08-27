from app.api.workspace import router


def test_virtual_snapshot_routes_are_exposed():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/source-folders/{external_id}/snapshots" in paths
    assert "/projects/{project_id}/source-folders/discover" in paths
    assert "/projects/{project_id}/source-folders/{external_id}/snapshot-queue" in paths
    assert "/projects/{project_id}/source-folders/{external_id}/primary" in paths
    assert "/projects/{project_id}/snapshots/{snapshot_id}/analyze" in paths


def test_snapshot_analysis_is_explicitly_read_only_in_contract():
    route = next(route for route in router.routes if route.path.endswith("/snapshots/{snapshot_id}/analyze"))
    assert "no Drive copy or mutation" in (route.endpoint.__doc__ or "")
