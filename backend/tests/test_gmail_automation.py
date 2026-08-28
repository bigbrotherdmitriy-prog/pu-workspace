from unittest.mock import patch

from app.automations import gmail


def test_gmail_automation_is_opt_in_outside_compose():
    with patch.dict("os.environ", {}, clear=True):
        assert gmail.enabled() is False
        assert gmail.interval_seconds() == 300


def test_gmail_automation_interval_has_safe_minimum():
    with patch.dict("os.environ", {"GMAIL_AUTO_SYNC_INTERVAL_SECONDS": "5"}, clear=True):
        assert gmail.interval_seconds() == 60


def test_gmail_automation_status_is_observable():
    with patch.dict("os.environ", {"GMAIL_AUTO_SYNC_ENABLED": "true", "GMAIL_AUTO_SYNC_INTERVAL_SECONDS": "600"}, clear=True):
        result = gmail.status()
        assert result["enabled"] is True
        assert result["interval_seconds"] == 600
