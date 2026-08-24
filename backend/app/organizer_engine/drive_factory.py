from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Callable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def _import_callable(spec: str) -> Callable[..., Any]:
    module_name, func_name = spec.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def get_drive_service(project_id: int | None = None, db: Any = None):
    """Return authenticated Google Drive v3 service.

    Preferred integration: set PU_DRIVE_SERVICE_FACTORY=module:function where
    function accepts project_id/db (or no args) and returns a Drive service.
    This lets PU Workspace reuse its existing OAuth storage.

    Fallback is token.json for migration from the supplied Telegram MVP.
    """
    spec = os.getenv("PU_DRIVE_SERVICE_FACTORY", "").strip()
    if spec:
        fn = _import_callable(spec)
        for kwargs in (
            {"project_id": project_id, "db": db},
            {"project_id": project_id},
            {"db": db},
            {},
        ):
            try:
                return fn(**kwargs)
            except TypeError:
                continue

    token_path = Path(os.getenv("GOOGLE_TOKEN_FILE", "/app/token.json"))
    if not token_path.exists():
        raise RuntimeError(
            "Google Drive service is not configured. Set PU_DRIVE_SERVICE_FACTORY "
            "to the existing PU Workspace OAuth service factory or provide GOOGLE_TOKEN_FILE."
        )
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        try:
            token_path.chmod(0o600)
        except OSError:
            pass
    return build("drive", "v3", credentials=creds, cache_discovery=False)
