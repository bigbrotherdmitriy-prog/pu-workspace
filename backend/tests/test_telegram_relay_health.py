from unittest.mock import patch

from app import telegram_relay


def test_relay_health_exposes_polling_without_secrets():
    with patch.dict("os.environ", {"TELEGRAM_POLLING_ENABLED": "true"}, clear=True):
        result = telegram_relay.health()
    assert result["status"] in {"healthy", "degraded"}
    assert result["polling_enabled"] is True
    assert {"last_poll_at", "last_update_id", "delivered_updates", "last_error"} <= result.keys()
    assert "token" not in result


def test_relay_error_redacts_bot_token():
    token = "123456:secret_value"
    error = RuntimeError(f"request failed at https://api.telegram.org/bot{token}/getUpdates")

    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token}, clear=False):
        result = telegram_relay._safe_error(error)

    assert token not in result
    assert "bot<redacted>/getUpdates" in result
