from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_integrations_ui_is_extracted_from_app_shell():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module_source = (
        ROOT / "frontend" / "src" / "modules" / "integrations" / "IntegrationsModule.tsx"
    ).read_text(encoding="utf-8")

    assert 'from "./modules/integrations/IntegrationsModule"' in app_source
    assert "<IntegrationsModule" in app_source
    assert "select_source" in module_source
    assert "google_workspace" not in module_source
