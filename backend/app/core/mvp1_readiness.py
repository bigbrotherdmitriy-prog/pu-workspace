"""Fail-closed readiness checks owned by the standalone MVP-1 app."""

import os

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine
from app.schema import CURRENT_SCHEMA_REVISION


def readiness_report() -> dict:
    checks: dict[str, dict] = {}
    app_secret = os.getenv("APP_SECRET_KEY", "")
    bootstrap = os.getenv("BOOTSTRAP_TOKEN", "")
    token_key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    checks["app_secret"] = _check(len(app_secret) >= 32, "configured" if len(app_secret) >= 32 else "minimum 32 characters")
    checks["bootstrap_token"] = _check(len(bootstrap) >= 24, "configured" if len(bootstrap) >= 24 else "minimum 24 characters")
    try:
        Fernet(token_key.encode("ascii"))
        checks["token_encryption"] = _check(True, "configured")
    except (ValueError, TypeError):
        checks["token_encryption"] = _check(False, "invalid or missing Fernet key")
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        checks["database"] = _check(True, "reachable")
        checks["schema"] = _check(revision == CURRENT_SCHEMA_REVISION, revision or "migration version missing")
    except SQLAlchemyError:
        checks["database"] = _check(False, "unreachable")
        checks["schema"] = _check(False, "unavailable")
    return {"ready": all(item["ok"] for item in checks.values()), "scope": "mvp1", "checks": checks}


def _check(ok: bool, message: str) -> dict:
    return {"ok": ok, "message": message}
