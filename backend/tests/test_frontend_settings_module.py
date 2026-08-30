from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "frontend/src/App.tsx"
MODULE = ROOT / "frontend/src/modules/settings/SettingsModule.tsx"


def test_settings_are_extracted_from_app_shell():
    app = APP.read_text(encoding="utf-8")
    module = MODULE.read_text(encoding="utf-8")

    assert 'from "./modules/settings/SettingsModule"' in app
    assert "<SettingsModule" in app
    assert "Очередь массовой обработки" not in app
    assert "Очередь массовой обработки" in module


def test_settings_preserve_security_and_recovery_controls():
    module = MODULE.read_text(encoding="utf-8")

    assert "PasswordChangeCard" in module
    assert "Только локально — внешний AI запрещён" in module
    assert "Проверять персональные данные" in module
    assert "Черновики ответов не отправляются без подтверждения" in module
    assert "onRetrySnapshot" in module
    assert "onRetrySession" in module
