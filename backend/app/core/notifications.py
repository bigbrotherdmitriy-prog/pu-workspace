from __future__ import annotations

import os

import httpx


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def notify_telegram(message: str) -> bool:
    """Best-effort notification; organizer work never fails because Telegram is unavailable."""
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    return notify_telegram_chat(chat_id, message)


def notify_telegram_chat(chat_id: str | int, message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:4000]},
            timeout=10.0,
        )
        response.raise_for_status()
        return True
    except Exception:
        return False
