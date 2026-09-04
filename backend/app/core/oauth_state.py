import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException


def _secret() -> bytes:
    value = os.getenv("APP_SECRET_KEY", "")
    if len(value) < 32:
        raise HTTPException(503, "APP_SECRET_KEY must contain at least 32 characters")
    return value.encode("utf-8")


def make_oauth_state(project_id: int, provider: str) -> str:
    payload = json.dumps(
        {"project_id": project_id, "provider": provider, "expires": int(time.time()) + 600, "nonce": secrets.token_urlsafe(16)},
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(_secret(), encoded, hashlib.sha256).digest()
    return (encoded + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


def project_from_oauth_state(state: str, provider: str) -> int:
    try:
        encoded, supplied = state.encode("ascii").split(b".", 1)
        expected = hmac.new(_secret(), encoded, hashlib.sha256).digest()
        signature = base64.urlsafe_b64decode(supplied + b"=" * (-len(supplied) % 4))
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4)))
        if int(payload["expires"]) < int(time.time()) or payload.get("provider", provider) != provider:
            raise ValueError("expired or wrong provider")
        return int(payload["project_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Invalid or expired OAuth state") from exc
