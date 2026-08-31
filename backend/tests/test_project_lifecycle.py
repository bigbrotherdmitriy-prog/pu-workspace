from pathlib import Path
from types import SimpleNamespace

from app.api.projects import _discover_project_safe_copies, _source_session_ready, router


def test_project_lifecycle_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}" in paths
    assert "/projects/{project_id}/restore" in paths
    assert "/projects/{project_id}/safe-copies" in paths
    assert "/projects/{project_id}/safe-copies/trash" in paths
    assert "/projects/{project_id}/launch-readiness" in paths


def test_project_launch_source_is_ready_after_safe_copy_analysis_finishes():
    assert _source_session_ready(SimpleNamespace(status="proposed", copy_folder_id="safe-copy"))
    assert _source_session_ready(SimpleNamespace(status="applied", copy_folder_id="safe-copy"))
    assert not _source_session_ready(SimpleNamespace(status="analyzing", copy_folder_id="safe-copy"))
    assert not _source_session_ready(SimpleNamespace(status="proposed", copy_folder_id=None))


def test_launch_readiness_exposes_canonical_completion_contract():
    source = (Path(__file__).resolve().parents[1] / "app" / "api" / "projects.py").read_text(encoding="utf-8")

    assert '"ready": completed == len(steps)' in source
    assert '"steps": steps' in source
    assert '"completed_steps": completed' in source


def test_frontend_keeps_cleanup_result_visible_on_project_card():
    app = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "project-cleanup-result" in app
    assert "Копии удалены:" in app
    assert "Можно архивировать проект" in app
    assert "copyCleanupResults[item.id]" in app


def test_cleanup_discovers_tracked_and_orphaned_safe_copies_only():
    sessions = [
        SimpleNamespace(
            source_folder_id="source-dci", source_folder_name="DCI",
            copy_folder_id="tracked-copy", copy_folder_name="DCI (безопасная копия 2026-08-31 05-23-58 UTC)",
        )
    ]

    class Scalars:
        def all(self):
            return sessions

        def __iter__(self):
            return iter(sessions)

    class Db:
        def scalars(self, _query):
            return Scalars()

    class Drive:
        def get_file_meta(self, file_id):
            assert file_id == "source-dci"
            return SimpleNamespace(parent_id="customer-folder")

        def list_children(self, folder_id):
            if folder_id == "root":
                return [
                    SimpleNamespace(id="other-project-copy", name="Мои (безопасная копия 2026-08-31 06-23-58 UTC)", is_folder=True),
                ]
            assert folder_id == "customer-folder"
            return [
                SimpleNamespace(id="source-dci", name="DCI", is_folder=True),
                SimpleNamespace(id="tracked-copy", name="DCI (безопасная копия 2026-08-31 05-23-58 UTC)", is_folder=True),
                SimpleNamespace(id="orphan-copy", name="DCI (безопасная копия 2026-08-31 06-23-58 UTC)", is_folder=True),
                SimpleNamespace(id="lookalike", name="DCI (безопасная копия вручную)", is_folder=True),
            ]

    copies = _discover_project_safe_copies(Db(), 7, Drive())
    assert set(copies) == {"tracked-copy", "orphan-copy"}
    assert copies["orphan-copy"] is None
