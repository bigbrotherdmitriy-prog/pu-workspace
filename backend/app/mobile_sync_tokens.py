"""Signed, non-authorizing base snapshots for offline three-way merge."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os


class MobileSyncTokenError(ValueError):
    pass


def _secret() -> bytes:
    value = os.getenv("APP_SECRET_KEY", "")
    if len(value) < 32:
        raise MobileSyncTokenError("mobile_sync_secret_unavailable")
    return value.encode("utf-8")


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_mobile_sync_token(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return f"{_encode(raw)}.{_encode(signature)}"


def maybe_issue_mobile_sync_token(payload: dict) -> str | None:
    """Keep legacy/offline test setups usable; readiness rejects this in production."""
    try:
        return issue_mobile_sync_token(payload)
    except MobileSyncTokenError:
        return None


def verify_mobile_sync_token(token: str) -> dict:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        raw = _decode(encoded_payload)
        supplied = _decode(encoded_signature)
    except (ValueError, TypeError) as exc:
        raise MobileSyncTokenError("invalid_mobile_sync_token") from exc
    expected = hmac.new(_secret(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied, expected):
        raise MobileSyncTokenError("invalid_mobile_sync_token")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MobileSyncTokenError("invalid_mobile_sync_token") from exc
    if not isinstance(payload, dict):
        raise MobileSyncTokenError("invalid_mobile_sync_token")
    return payload
