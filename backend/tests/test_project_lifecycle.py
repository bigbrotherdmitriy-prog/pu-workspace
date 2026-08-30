from pathlib import Path
from types import SimpleNamespace

from app.api.projects import _source_session_ready, router


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
