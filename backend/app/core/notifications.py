"""Compatibility exports. New code imports the Telegram channel adapter directly."""

from app.integrations.telegram import (
    notify_telegram,
    notify_telegram_chat,
    telegram_configured,
    telegram_http_client,
)

__all__ = ["notify_telegram", "notify_telegram_chat", "telegram_configured", "telegram_http_client"]
