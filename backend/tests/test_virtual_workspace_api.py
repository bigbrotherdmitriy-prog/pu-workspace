from app.api.workspace import _drive_folder_breadcrumb, router


def test_virtual_snapshot_routes_are_exposed():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/source-folders/{external_id}/snapshots" in paths
    assert "/projects/{project_id}/source-folders/discover" in paths
    assert "/projects/{project_id}/source-folders/{external_id}/snapshot-queue" in paths
    assert "/projects/{project_id}/source-folders/snapshot-queue-all" in paths
    assert "/projects/{project_id}/source-folders/{external_id}/primary" in paths
    assert "/projects/{project_id}/snapshots/{snapshot_id}/analyze" in paths


def test_snapshot_analysis_is_explicitly_read_only_in_contract():
    route = next(route for route in router.routes if route.path.endswith("/snapshots/{snapshot_id}/analyze"))
    assert "no Drive copy or mutation" in (route.endpoint.__doc__ or "")


def test_nested_drive_breadcrumb_is_root_to_current_folder():
    folders = {
        "customer": {"id": "customer", "name": "Заказчик", "parents": ["root"]},
        "project": {"id": "project", "name": "Проект 1", "parents": ["customer"]},
    }

    class Request:
        def __init__(self, item):
            self.item = item

        def execute(self):
            return self.item

    class Files:
        def get(self, *, fileId, fields, supportsAllDrives):
            assert fields == "id,name,parents"
            assert supportsAllDrives is True
            return Request(folders[fileId])

    class Service:
        def files(self):
            return Files()

    assert _drive_folder_breadcrumb(Service(), "project") == [
        {"id": "root", "name": "Мой диск"},
        {"id": "customer", "name": "Заказчик"},
        {"id": "project", "name": "Проект 1"},
    ]
