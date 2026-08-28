from pathlib import Path


APP_SOURCE = Path(__file__).parents[2] / "frontend" / "src" / "App.tsx"


def test_new_project_reload_uses_created_project_id():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "async function load(preferredProjectId?: number)" in source
    assert "await load(created.id);" in source
    assert 'sessionStorage.setItem("pu_active_project_id", String(created.id));' in source


def test_oauth_callback_restores_project_before_initial_load():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert 'new URLSearchParams(window.location.search).get("project_id")' in source
    assert "[projectId, setProjectId] = useState(restoredProjectId)" in source
