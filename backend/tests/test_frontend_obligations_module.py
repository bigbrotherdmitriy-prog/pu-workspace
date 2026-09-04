from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "frontend" / "src" / "App.tsx"
MODULE = ROOT / "frontend" / "src" / "modules" / "obligations" / "ObligationsModule.tsx"


def test_obligations_registry_is_extracted_from_app_shell() -> None:
    app = APP.read_text(encoding="utf-8")
    module = MODULE.read_text(encoding="utf-8")

    assert 'from "./modules/obligations/ObligationsModule"' in app
    assert "<ObligationsModule" in app
    assert "management-list" not in app
    assert "management-list" in module
    assert "Реестр обязательств" in module


def test_obligation_actions_keep_human_confirmation_flow() -> None:
    module = MODULE.read_text(encoding="utf-8")

    assert 'onUpdate(item, "confirmed")' in module
    assert 'onUpdate(item, "in_progress")' in module
    assert 'onUpdate(item, "fulfilled")' in module
    assert "Каждый вывод хранит источник и требует подтверждения" in module
