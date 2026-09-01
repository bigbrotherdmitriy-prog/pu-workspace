import os
import shutil
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine
from app.schema import CURRENT_SCHEMA_REVISION
from app.automations.gmail import status as gmail_automation_status
from app.automations.ai_secretary import status as ai_secretary_automation_status
from app.database import SessionLocal
from app.models.job import BackgroundJob, ServiceHeartbeat


def readiness_report() -> dict:
    checks: dict[str, dict] = {}
    durable_execution = os.getenv("PU_BACKGROUND_EXECUTION", "in_process") == "durable"
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
    gmail_automation = gmail_automation_status()
    last_result = gmail_automation.get("last_result") or {}
    automation_message = (
        f"running every {gmail_automation['interval_seconds']} seconds"
        f"; last run {gmail_automation['last_run_at'] or 'pending'}"
        f"; processed {last_result.get('processed', 0)}; failed {last_result.get('failed', 0)}"
    )
    checks["gmail_automation"] = _check(
        durable_execution or not gmail_automation["enabled"] or (gmail_automation["running"] and not gmail_automation["last_error"]),
        (
            "managed by durable scheduler"
            if durable_execution else
            automation_message
            if gmail_automation["running"]
            else "disabled" if not gmail_automation["enabled"] else "not running"
        ),
        required=False,
    )
    ai_automation = ai_secretary_automation_status()
    ai_last_result = ai_automation.get("last_result") or {}
    checks["ai_secretary_automation"] = _check(
        durable_execution or not ai_automation["enabled"] or (ai_automation["running"] and not ai_automation["last_error"]),
        (
            "managed by durable scheduler"
            if durable_execution else
            f"running every {ai_automation['interval_seconds']} seconds; "
            f"last run {ai_automation['last_run_at'] or 'pending'}; "
            f"prepared {ai_last_result.get('prepared', 0)}"
            if ai_automation["running"] else "disabled" if not ai_automation["enabled"] else "not running"
        ),
        required=False,
    )
    ocr_tools = {name: bool(shutil.which(name)) for name in ("tesseract", "pdftoppm")}
    ocr_enabled = os.getenv("OCR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    checks["local_ocr"] = _check(
        not ocr_enabled or all(ocr_tools.values()),
        (
            "ready (rus+eng; local processing)"
            if ocr_enabled and all(ocr_tools.values())
            else "disabled"
            if not ocr_enabled
            else "missing: " + ", ".join(name for name, available in ocr_tools.items() if not available)
        ),
        required=False,
    )

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        checks["database"] = _check(True, "reachable")
        checks["schema"] = _check(revision == CURRENT_SCHEMA_REVISION, revision or "migration version missing")
        if durable_execution:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=90)
            with SessionLocal() as db:
                worker_count = len(list(db.scalars(select(ServiceHeartbeat.service_id).where(
                    ServiceHeartbeat.service_kind == "worker", ServiceHeartbeat.last_seen >= cutoff,
                ))))
                scheduler_count = len(list(db.scalars(select(ServiceHeartbeat.service_id).where(
                    ServiceHeartbeat.service_kind == "scheduler", ServiceHeartbeat.last_seen >= cutoff,
                ))))
                dead_letters = len(list(db.scalars(select(BackgroundJob.id).where(
                    BackgroundJob.status == "dead_letter",
                ))))
            checks["durable_workers"] = _check(worker_count >= 2, f"active workers: {worker_count}; required: 2")
            checks["durable_scheduler"] = _check(scheduler_count >= 1, f"active schedulers: {scheduler_count}; required: 1")
            checks["dead_letter_queue"] = _check(dead_letters == 0, f"dead-letter jobs: {dead_letters}", required=False)
    except SQLAlchemyError as exc:
        checks["database"] = _check(False, exc.__class__.__name__)
        checks["schema"] = _check(False, "database unavailable")

    required_ready = all(item["ok"] for item in checks.values() if item["required"])
    return {"ready": required_ready, "google_drive_ready": google_ready, "telegram_ready": telegram_ready, "checks": checks}


def _check(ok: bool, message: str, required: bool = True) -> dict:
    return {"ok": ok, "required": required, "message": message}
