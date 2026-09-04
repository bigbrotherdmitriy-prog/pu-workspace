from datetime import date
from unittest.mock import patch

from app.automation_engine import following_monthly_date, monthly_date, next_monthly_date
from app.automations import ai_secretary


def test_monthly_date_uses_last_day_for_short_month():
    assert monthly_date(2027, 2, 31) == date(2027, 2, 28)


def test_next_monthly_date_keeps_today_and_advances_after_it():
    assert next_monthly_date(15, date(2026, 8, 15)) == date(2026, 8, 15)
    assert next_monthly_date(15, date(2026, 8, 16)) == date(2026, 9, 15)


def test_following_monthly_date_rolls_year():
    assert following_monthly_date(10, date(2026, 12, 10)) == date(2027, 1, 10)


def test_automation_interval_has_safe_minimum_and_status_is_observable():
    with patch.dict(
        "os.environ",
        {"AI_SECRETARY_AUTOMATION_ENABLED": "true", "AI_SECRETARY_AUTOMATION_INTERVAL_SECONDS": "5"},
        clear=True,
    ):
        assert ai_secretary.enabled() is True
        assert ai_secretary.interval_seconds() == 60
        assert {"running", "last_run_at", "last_result", "last_error"} <= ai_secretary.status().keys()
