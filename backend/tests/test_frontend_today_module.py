from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_today_screen_uses_provider_neutral_daily_briefing():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module = (ROOT / "frontend" / "src" / "modules" / "today" / "TodayModule.tsx").read_text(encoding="utf-8")

    assert 'from "./modules/today/TodayModule"' in app
    assert '[CalendarDays, "Сегодня"]' in app
    assert 'active === "Сегодня"' in app
    assert "Три следующих действия" in module
    assert "dailyBriefing" in app
    assert "google" not in module.casefold()
