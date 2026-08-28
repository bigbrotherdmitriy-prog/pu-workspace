from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, request_timeout: int = 60) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.request_timeout = max(10, min(90, request_timeout))

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        encoded = urllib.parse.urlencode(self._normalize(payload or {})).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/{method}", data=encoded)
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TelegramError(f"Telegram request failed: {exc}") from exc
        if not result.get("ok"):
            raise TelegramError(result.get("description", "Unknown Telegram API error"))
        return result.get("result")

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in payload.items():
            result[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
        return result

    def send_message(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self.call("sendMessage", payload)

    def send_photo(
        self,
        chat_id: int,
        photo: str,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self.call("sendPhoto", payload)

    def answer_callback(self, callback_query_id: str) -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_query_id})

    def get_updates(self, offset: int, timeout: int) -> list[dict[str, Any]]:
        return self.call(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
        )
