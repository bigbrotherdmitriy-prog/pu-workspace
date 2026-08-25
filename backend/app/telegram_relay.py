from __future__ import annotations
import hmac
import os
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="PU Workspace Telegram Relay")


class SendRequest(BaseModel):
    chat_id: str | int
    message: str


def _check(secret: str | None):
    expected = os.getenv("TELEGRAM_RELAY_SECRET", "")
    if not expected or not secret or not hmac.compare_digest(expected, secret):
        raise HTTPException(403, "Forbidden")


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(local_address="::"), timeout=30.0)


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
        meta = client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id})
        meta.raise_for_status()
        path = meta.json()["result"]["file_path"]
        data = client.get(f"https://api.telegram.org/file/bot{token}/{path}")
        data.raise_for_status()
    return Response(data.content, media_type="application/octet-stream")
