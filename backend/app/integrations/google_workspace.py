from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.token_crypto import TokenEncryptionError, decrypt_token, encrypt_token
from app.integrations.contracts import AdapterHealth
from app.models.google_token import GoogleOAuthToken


class GoogleWorkspaceAdapter:
    """Google account boundary shared by Drive, Gmail, Tasks and Calendar adapters."""

    provider = "google_workspace"

    def __init__(self, project_id: int | None, db: Session, *, token_id: int | None = None):
        self.project_id = project_id
        self.db = db
        self.token_id = token_id

    def _token_query(self):
        return (GoogleOAuthToken.id == self.token_id) if self.token_id is not None else (GoogleOAuthToken.project_id == self.project_id)

    def configured(self) -> bool:
        return bool(
            os.getenv("GOOGLE_CLIENT_ID")
            and os.getenv("GOOGLE_CLIENT_SECRET")
            and os.getenv("GOOGLE_REDIRECT_URI")
        )

    def health(self) -> AdapterHealth:
        token = self.db.scalar(select(GoogleOAuthToken).where(self._token_query()))
        ready = token is not None and bool(token.access_token) and self.configured()
        return AdapterHealth(ready=ready, detail="connected" if ready else "not connected")

    def authorized_scopes(self) -> frozenset[str]:
        token = self.db.scalar(
            select(GoogleOAuthToken.scopes).where(
                self._token_query()
            )
        )
        return frozenset((token or "").split())

    def capability_connected(self, scope: str) -> bool:
        return self.health().ready and scope in self.authorized_scopes()

    def credentials(self) -> Credentials:
        token = self.db.scalar(select(GoogleOAuthToken).where(self._token_query()))
        if token is None or token.access_token is None:
            raise HTTPException(status_code=401, detail="Google Workspace is not authorized")

        try:
            access_token = decrypt_token(token.access_token)
            refresh_token = decrypt_token(token.refresh_token)
        except TokenEncryptionError as exc:
            raise HTTPException(503, str(exc)) from exc

        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=token.token_uri,
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=(token.scopes or "").split(),
        )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token.access_token = encrypt_token(credentials.token)
            if credentials.refresh_token:
                token.refresh_token = encrypt_token(credentials.refresh_token)
            self.db.commit()
        return credentials

    def service(self, api: str, version: str) -> Any:
        return build(api, version, credentials=self.credentials(), cache_discovery=False)


def google_workspace_for_project(project_id: int, db: Session) -> GoogleWorkspaceAdapter:
    return GoogleWorkspaceAdapter(project_id, db)


def google_workspace_for_mailbox(google_token_id: int, db: Session) -> GoogleWorkspaceAdapter:
    """Use only a generation-pinned token resolved from persisted message origin."""
    return GoogleWorkspaceAdapter(None, db, token_id=google_token_id)


def credentials_for_project(project_id: int, db: Session) -> Credentials:
    """Compatibility function for integrations that still consume credentials."""
    return google_workspace_for_project(project_id, db).credentials()
