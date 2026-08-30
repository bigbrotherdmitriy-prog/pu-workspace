from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_analytics_is_extracted_from_app_monolith():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module = (ROOT / "frontend" / "src" / "modules" / "analytics" / "AnalyticsModule.tsx").read_text(encoding="utf-8")

    assert 'from "./modules/analytics/AnalyticsModule"' in app
    assert "<AnalyticsModule" in app
    assert "analytics-hero" in module
    assert "analytics-metrics" in module
    assert "analytics-grid" in module
    assert "analytics-hero" not in app


def test_analytics_module_remains_provider_neutral_and_read_only():
    module = (ROOT / "frontend" / "src" / "modules" / "analytics" / "AnalyticsModule.tsx").read_text(encoding="utf-8")

    assert "независимо от подключённых сервисов" in module
    assert "google" not in module.casefold()
    assert "api(" not in module
