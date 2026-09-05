from datetime import datetime, timedelta, timezone
import os

import httpx
from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token, encrypt_token
from app.integrations.contracts import StorageCredentialsExpired, StorageUnavailable
from app.models.audit_log import AuditLog
from app.models.integration_credential import IntegrationCredential


TOKEN_URL = "https://oauth.yandex.ru/token"


def refresh_yandex_credential(row: IntegrationCredential, db: Session) -> str:
    client_id = os.getenv("YANDEX_CLIENT_ID", "").strip()
    client_secret = os.getenv("YANDEX_CLIENT_SECRET", "").strip()
    refresh_token = decrypt_token(row.refresh_token)
    if not client_id or not client_secret or not refresh_token:
        raise StorageCredentialsExpired("Yandex refresh token is unavailable; reconnect the account")
    try:
        response = httpx.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": client_id, "client_secret": client_secret,
        }, timeout=20.0)
    except httpx.HTTPError as exc:
        raise StorageUnavailable("Yandex OAuth is temporarily unavailable") from exc
    if response.status_code >= 400:
        raise StorageCredentialsExpired("Yandex credentials expired; reconnect the account")
    payload = response.json()
    row.access_token = encrypt_token(payload["access_token"])
    row.refresh_token = encrypt_token(payload.get("refresh_token") or refresh_token)
    row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in") or 0)) if payload.get("expires_in") else None
    db.add(AuditLog(action="storage_credentials_refreshed", entity_type="project", entity_id=row.project_id, details="provider=yandex_disk"))
    db.commit()
    return payload["access_token"]


def credential_expiring(row: IntegrationCredential) -> bool:
    if not row.expires_at:
        return False
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    return expires <= datetime.now(timezone.utc) + timedelta(minutes=2)
