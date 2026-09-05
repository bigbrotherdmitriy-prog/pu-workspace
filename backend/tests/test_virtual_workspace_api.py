import inspect

from app.api.workspace import (
    MANAGED_PROJECT_STRUCTURE,
    _build_snapshot,
    _drive_folder_breadcrumb,
    _run_safe_copy_pipeline,
    recover_incomplete_analyses,
    router,
)


def test_virtual_snapshot_routes_are_exposed():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/source-folders/{external_id:path}/snapshots" in paths
    assert "/projects/{project_id}/source-folders/discover" in paths
    assert "/projects/{project_id}/source-folders/{external_id:path}/snapshot-queue" in paths
    assert "/projects/{project_id}/source-folders/snapshot-queue-all" in paths
    assert "/projects/{project_id}/source-folders/{external_id:path}/primary" in paths
    assert "/projects/{project_id}/snapshots/{snapshot_id}/analyze" in paths
    assert "/projects/{project_id}/snapshots/{snapshot_id}/standardize" in paths
    assert "/projects/{project_id}/managed-workspace" in paths


def test_managed_project_structure_covers_core_project_domains():
    names = {name for name, _ in MANAGED_PROJECT_STRUCTURE}
    assert {"01_Договоры", "03_ГПР", "04_Финансы", "05_Переписка", "99_Архив"} <= names
    finance = dict(MANAGED_PROJECT_STRUCTURE)["04_Финансы"]
    assert {"01_Бюджет", "02_ДДС", "03_Счета_и_оплаты"} <= set(finance)


def test_snapshot_analysis_is_explicitly_read_only_in_contract():
    route = next(route for route in router.routes if route.path.endswith("/snapshots/{snapshot_id}/analyze"))
    assert "no Drive copy or mutation" in (route.endpoint.__doc__ or "")


def test_connected_folder_snapshot_does_not_automatically_create_safe_copy():
    source = inspect.getsource(_build_snapshot)
    assert "_start_safe_copy_pipeline(snapshot_id, project_id, external_id, source_name)" not in source
    standardize = next(route.endpoint for route in router.routes if route.path.endswith("/snapshots/{snapshot_id}/standardize"))
    assert "Create and organize a safe Drive copy" in (standardize.__doc__ or "")


def test_safe_copy_recovery_is_not_started_by_legacy_virtual_analyzer():
    source = inspect.getsource(recover_incomplete_analyses)
    assert 'get("mode") != "safe_copy"' in source


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
