from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_settings_exposes_safe_self_service_password_change():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    component = (
        ROOT / "frontend" / "src" / "modules" / "settings" / "PasswordChangeCard.tsx"
    ).read_text(encoding="utf-8")

    assert 'from "./modules/settings/PasswordChangeCard"' in app
    assert "<PasswordChangeCard" in app
    assert 'api("/auth/change-password"' in component
    assert 'sessionStorage.removeItem("pu_token")' in component
    assert 'autoComplete="current-password"' in component
    assert component.count('autoComplete="new-password"') == 2
    assert "не менее 12 символов" in component
