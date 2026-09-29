from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import os
import re
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.external_retry import RATE_LIMIT_ONLY_RETRY, request_with_retry
from app.integrations.telegram import telegram_force_ipv6


_poll_state = {
    "last_poll_at": None,
    "last_update_id": None,
    "delivered_updates": 0,
    "last_error": None,
    "last_error_at": None,
    "last_error_operation": None,
    "last_error_type": None,
    "last_error_status": None,
    "errors_total": 0,
    "consecutive_errors": 0,
    "recoveries_total": 0,
    "last_recovered_at": None,
}

# A successful getUpdates poll can take 25 seconds. A pending backend delivery
# has a separate 120-second timeout and must not make a stale poll look healthy.
POLL_FRESHNESS_SECONDS = 90

_send_state = {
    "successes_total": 0, "errors_total": 0,
    "last_success_at": None, "last_error_at": None,
    "last_error_type": None, "last_error_status": None,
}


def _record_poll_failure(operation: str, exc: Exception) -> float:
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    _poll_state.update(
        last_error=f"{operation}: {type(exc).__name__}" + (f" HTTP {status}" if status else ""),
        last_error_at=_utc_now(), last_error_operation=operation,
        last_error_type=type(exc).__name__, last_error_status=status,
        errors_total=_poll_state["errors_total"] + 1,
        consecutive_errors=_poll_state["consecutive_errors"] + 1,
    )
    # Never log request URLs, tokens, response bodies, or message content.
    print(f"[TELEGRAM POLLING] {_poll_state['last_error']}", flush=True)
    return min(30, 3 * 2 ** min(_poll_state["consecutive_errors"] - 1, 4))


def _record_poll_success() -> None:
    if _poll_state["consecutive_errors"]:
        _poll_state["recoveries_total"] += 1
        _poll_state["last_recovered_at"] = _utc_now()
    _poll_state.update(last_poll_at=_utc_now(), last_error=None, consecutive_errors=0)


def _require_telegram_ok(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("telegram_response_not_ok")
    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _polling_enabled() -> bool:
    return os.getenv("TELEGRAM_POLLING_ENABLED", "true").lower() in {"1", "true", "yes"}


def _safe_error(exc: Exception) -> str:
    """Keep diagnostics useful without exposing the bot credential in URLs."""
    message = str(exc)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if token:
        message = message.replace(token, "<redacted>")
    return re.sub(r"(?<=/bot)[0-9]+:[A-Za-z0-9_-]+", "<redacted>", message)


def _force_ipv6() -> bool:
    return telegram_force_ipv6()


async def _poll_updates() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    webhook_secret = os.environ["TELEGRAM_WEBHOOK_SECRET"]
    backend_url = os.getenv("TELEGRAM_BACKEND_WEBHOOK_URL", "http://127.0.0.1:3000/telegram/webhook")
    api = f"https://api.telegram.org/bot{token}"
    offset: int | None = None
    telegram_options = {"timeout": 35.0}
    if _force_ipv6():
        telegram_options["transport"] = httpx.AsyncHTTPTransport(local_address="::")
    async with httpx.AsyncClient(**telegram_options) as telegram, httpx.AsyncClient(timeout=120.0) as backend:
        # Long polling and webhooks are mutually exclusive. Keep queued updates.
        initialized = False
        while True:
            operation = "deleteWebhook" if not initialized else "getUpdates"
            try:
                if not initialized:
                    response = await telegram.post(f"{api}/deleteWebhook", json={"drop_pending_updates": False})
                    _require_telegram_ok(response)
                    initialized = True
                operation = "getUpdates"
                params = {"timeout": 25, "allowed_updates": '["message","edited_message"]'}
                if offset is not None:
                    params["offset"] = offset
                response = await telegram.get(f"{api}/getUpdates", params=params)
                updates = _require_telegram_ok(response)["result"]
                if not isinstance(updates, list):
                    raise ValueError("invalid_updates")
                for update in updates:
                    operation = "backend_webhook"
                    delivered = await backend.post(
                        backend_url,
                        json=update,
                        headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
                    )
                    delivered.raise_for_status()
                    offset = int(update["update_id"]) + 1
                    _poll_state["last_update_id"] = int(update["update_id"])
                    _poll_state["delivered_updates"] += 1
                _record_poll_success()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await asyncio.sleep(_record_poll_failure(operation, exc))


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_poll_updates()) if _polling_enabled() else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="PU Workspace Telegram Relay", lifespan=lifespan)


@app.get("/health")
def health():
    enabled = _polling_enabled()
    last = _poll_state["last_poll_at"]
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() if last else None
    fresh = age is not None and 0 <= age <= POLL_FRESHNESS_SECONDS
    return {
        "status": "healthy" if enabled and fresh and _poll_state["last_error"] is None else "degraded",
        "polling_enabled": enabled,
        "poll_age_seconds": age,
        "poll_freshness_seconds": POLL_FRESHNESS_SECONDS,
        "history_scope": "current_process",
        "outbound": dict(_send_state),
        **_poll_state,
    }


class SendRequest(BaseModel):
    chat_id: str | int
    message: str


def _check(secret: str | None):
    expected = os.getenv("TELEGRAM_RELAY_SECRET", "")
    if not expected or not secret or not hmac.compare_digest(expected, secret):
        raise HTTPException(403, "Forbidden")


def _client() -> httpx.Client:
    if _force_ipv6():
        return httpx.Client(transport=httpx.HTTPTransport(local_address="::"), timeout=30.0)
    return httpx.Client(timeout=30.0)


def _get_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    try:
        return request_with_retry(client, "GET", url, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Telegram file service is temporarily unavailable") from exc


@app.post("/send")
def send(payload: SendRequest, x_relay_secret: str | None = Header(default=None)):
    _check(x_relay_secret)
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        with _client() as client:
            response = request_with_retry(
                client, "POST", f"https://api.telegram.org/bot{token}/sendMessage",
                policy=RATE_LIMIT_ONLY_RETRY,
                json={"chat_id": payload.chat_id, "text": payload.message[:4000]},
            )
            _require_telegram_ok(response)
    except (httpx.HTTPError, ValueError) as exc:
        _send_state.update(
            errors_total=_send_state["errors_total"] + 1,
            last_error_at=_utc_now(), last_error_type=type(exc).__name__,
            last_error_status=exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
        )
        raise HTTPException(502, "Telegram delivery was not confirmed") from None
    _send_state.update(successes_total=_send_state["successes_total"] + 1, last_success_at=_utc_now())
    return {"ok": True}


@app.get("/file/{file_id}")
def file(file_id: str, x_relay_secret: str | None = Header(default=None)):
    _check(x_relay_secret)
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    with _client() as client:
        meta = _get_with_retry(client, f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id})
        path = meta.json()["result"]["file_path"]
        data = _get_with_retry(client, f"https://api.telegram.org/file/bot{token}/{path}")
    return Response(data.content, media_type="application/octet-stream")
