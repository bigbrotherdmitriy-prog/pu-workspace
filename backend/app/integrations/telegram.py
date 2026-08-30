from __future__ import annotations

import os

import httpx

from app.integrations.contracts import AdapterHealth, ChannelMessage


def telegram_force_ipv6() -> bool:
    """Bind to IPv6 only for direct connections, never when a proxy is configured."""
    proxy_configured = any(
        os.getenv(name, "").strip()
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
    )
    requested = os.getenv("TELEGRAM_FORCE_IPV6", "").lower() in {"1", "true", "yes"}
    return requested and not proxy_configured


def telegram_http_client(timeout: float = 10.0) -> httpx.Client:
    """Use IPv6 only for direct deployments; proxy routing remains environment-driven."""
    transport = httpx.HTTPTransport(local_address="::") if telegram_force_ipv6() else None
    return httpx.Client(transport=transport, timeout=timeout)


class TelegramChannelAdapter:
    provider = "telegram"

    def health(self) -> AdapterHealth:
        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            return AdapterHealth(ready=False, detail="bot token is not configured")
        relay = os.getenv("TELEGRAM_RELAY_URL", "").rstrip("/")
        if not relay:
            return AdapterHealth(ready=True, detail="configured")
        try:
            response = httpx.get(f"{relay}/health", timeout=3.0)
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") == "healthy":
                return AdapterHealth(ready=True, detail="relay healthy")
            error = str(payload.get("last_error") or "relay degraded")
            detail = "polling conflict: another bot process is active" if "409 Conflict" in error else "relay degraded"
            return AdapterHealth(ready=False, detail=detail)
        except Exception:
            return AdapterHealth(ready=False, detail="relay is unreachable")

    def receive(self, limit: int = 100) -> list[ChannelMessage]:
        # Telegram messages are pushed to the webhook/relay; polling is owned by
        # telegram_relay and not duplicated in the adapter.
        return []

    def send(self, destination: str, text: str) -> str:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token or not destination:
            raise RuntimeError("Telegram is not configured")
        relay = os.getenv("TELEGRAM_RELAY_URL", "")
        relay_secret = os.getenv("TELEGRAM_RELAY_SECRET", "")
        if relay and relay_secret:
            response = httpx.post(
                f"{relay.rstrip('/')}/send",
                json={"chat_id": destination, "message": text[:4000]},
                headers={"X-Relay-Secret": relay_secret},
                timeout=12.0,
            )
        else:
            with telegram_http_client() as client:
                response = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": destination, "text": text[:4000]},
                )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        return str(payload.get("result", {}).get("message_id", "sent"))


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def notify_telegram(message: str) -> bool:
    return notify_telegram_chat(os.getenv("TELEGRAM_CHAT_ID", ""), message)


def notify_telegram_chat(chat_id: str | int, message: str) -> bool:
    """Best effort: a channel failure must never fail domain processing."""
    try:
        TelegramChannelAdapter().send(str(chat_id), message)
        return True
    except Exception:
        return False
