from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import os
import time
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel


def _polling_enabled() -> bool:
    return os.getenv("TELEGRAM_POLLING_ENABLED", "true").lower() in {"1", "true", "yes"}


async def _poll_updates() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    webhook_secret = os.environ["TELEGRAM_WEBHOOK_SECRET"]
    backend_url = os.getenv("TELEGRAM_BACKEND_WEBHOOK_URL", "http://127.0.0.1:3000/telegram/webhook")
    api = f"https://api.telegram.org/bot{token}"
    offset: int | None = None
    transport = httpx.AsyncHTTPTransport(local_address="::")
    async with httpx.AsyncClient(transport=transport, timeout=35.0) as telegram, httpx.AsyncClient(timeout=120.0) as backend:
        # Long polling and webhooks are mutually exclusive. Keep queued updates.
        response = await telegram.post(f"{api}/deleteWebhook", json={"drop_pending_updates": False})
        response.raise_for_status()
        while True:
            try:
                params = {"timeout": 25, "allowed_updates": '["message","edited_message"]'}
                if offset is not None:
                    params["offset"] = offset
                response = await telegram.get(f"{api}/getUpdates", params=params)
                response.raise_for_status()
                for update in response.json().get("result", []):
                    delivered = await backend.post(
                        backend_url,
                        json=update,
                        headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
                    )
                    delivered.raise_for_status()
                    offset = int(update["update_id"]) + 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[TELEGRAM POLLING] {exc.__class__.__name__}: {str(exc)[:300]}", flush=True)
                await asyncio.sleep(3)


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


class SendRequest(BaseModel):
    chat_id: str | int
    message: str


def _check(secret: str | None):
    expected = os.getenv("TELEGRAM_RELAY_SECRET", "")
    if not expected or not secret or not hmac.compare_digest(expected, secret):
        raise HTTPException(403, "Forbidden")


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(local_address="::"), timeout=30.0)


def _get_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise HTTPException(502, "Telegram file service is temporarily unavailable") from last_error


@app.post("/send")
def send(payload: SendRequest, x_relay_secret: str | None = Header(default=None)):
    _check(x_relay_secret)
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    with _client() as client:
        result = client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": payload.chat_id, "text": payload.message[:4000]})
        result.raise_for_status()
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
