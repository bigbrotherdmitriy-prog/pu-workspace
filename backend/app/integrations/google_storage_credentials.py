"""Google implementation behind the provider-neutral storage credential port."""

from __future__ import annotations

import os

from fastapi import HTTPException
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.core.token_crypto import TokenEncryptionError, decrypt_token, encrypt_token
from app.integrations.storage_credentials import ResolvedStorageCredential


def google_storage_credentials(resolved: ResolvedStorageCredential, db: Session) -> Credentials:
    if resolved.provider != "google_drive" or resolved.capability != "storage":
        raise HTTPException(409, "Selected credential is not a Google Drive storage credential")
    row = resolved.row
    try:
        access_token = decrypt_token(row.access_token)
        refresh_token = decrypt_token(row.refresh_token)
    except TokenEncryptionError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not access_token:
        raise HTTPException(401, "Google Drive is not authorized")
    client_id = (os.getenv("GOOGLE_STORAGE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_STORAGE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(503, "MVP-1 Google Drive OAuth is not configured")
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=row.token_uri or "https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=(row.scopes or "").split(),
    )
    if credentials.expired:
        if not credentials.refresh_token:
            raise HTTPException(401, "Google Drive credentials expired; reconnect the account")
        credentials.refresh(Request())
        row.access_token = encrypt_token(credentials.token)
        if credentials.refresh_token:
            row.refresh_token = encrypt_token(credentials.refresh_token)
        row.expires_at = credentials.expiry
        db.commit()
    return credentials


def google_storage_service(resolved: ResolvedStorageCredential, db: Session):
    return build(
        "drive", "v3", credentials=google_storage_credentials(resolved, db),
        cache_discovery=False,
    )
