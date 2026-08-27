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

    google_values = [os.getenv(name, "") for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI")]
    google_ready = all(google_values)
    checks["google_oauth"] = _check(google_ready, "configured" if google_ready else "credentials not configured", required=False)
    telegram_ready = bool(os.getenv("TELEGRAM_BOT_TOKEN", "")) and bool(
        os.getenv("TELEGRAM_RELAY_URL", "") or os.getenv("TELEGRAM_CHAT_ID", "")
    )
    checks["telegram"] = _check(
        telegram_ready,
        "configured" if telegram_ready else "bot or relay not configured",
        required=False,
    )

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        checks["database"] = _check(True, "reachable")
        checks["schema"] = _check(revision == CURRENT_SCHEMA_REVISION, revision or "migration version missing")
    except SQLAlchemyError as exc:
        checks["database"] = _check(False, exc.__class__.__name__)
        checks["schema"] = _check(False, "database unavailable")

    required_ready = all(item["ok"] for item in checks.values() if item["required"])
    return {"ready": required_ready, "google_drive_ready": google_ready, "telegram_ready": telegram_ready, "checks": checks}


def _check(ok: bool, message: str, required: bool = True) -> dict:
    return {"ok": ok, "required": required, "message": message}
