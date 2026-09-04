from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_notifications_screen_is_extracted_from_app_shell():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module_source = (ROOT / "frontend" / "src" / "modules" / "notifications" / "NotificationsModule.tsx").read_text(encoding="utf-8")

    assert 'from "./modules/notifications/NotificationsModule"' in app_source
    assert "<NotificationsModule" in app_source
    assert "Центр уведомлений" in module_source
    assert "notification-list" in module_source
