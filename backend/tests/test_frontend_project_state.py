from pathlib import Path


APP_SOURCE = Path(__file__).parents[2] / "frontend" / "src" / "App.tsx"


def test_new_project_reload_uses_created_project_id():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "async function load(preferredProjectId?: number)" in source
    assert "await activateProject(created.id);" in source
    assert 'sessionStorage.setItem("pu_active_project_id", String(id));' in source


def test_oauth_callback_restores_project_before_initial_load():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert 'new URLSearchParams(window.location.search).get("project_id")' in source
    assert "[projectId, setProjectId] = useState(restoredProjectId)" in source


def test_project_switch_is_persisted_and_stale_loads_cannot_restore_old_project():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "async function activateProject(id: number)" in source
    assert "projectIdRef.current = id;" in source
    assert "const loadSequence = ++loadSequenceRef.current;" in source
    assert "loadSequence !== loadSequenceRef.current" in source
    assert "onClick={() => activateProject(item.id)}" in source


def test_legacy_ui_keeps_selected_project_after_refresh_and_oauth():
    source = (APP_SOURCE.parents[2] / "backend" / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "function rememberedProjectId()" in source
    assert "async function projects(preferredId=0)" in source
    assert "await projects(created.id)" in source
    assert "projects(rememberedProjectId())" in source
