from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from threading import Event, Thread

from app.automation_engine import run_due_rules
from app.database import SessionLocal

log = logging.getLogger(__name__)
_stop = Event()
_thread: Thread | None = None
_last_run_at: str | None = None
_last_result: dict[str, int] | None = None
_last_error: str | None = None


def enabled() -> bool:
    return os.getenv("AI_SECRETARY_AUTOMATION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def interval_seconds() -> int:
    return max(60, int(os.getenv("AI_SECRETARY_AUTOMATION_INTERVAL_SECONDS", "300")))


def _worker() -> None:
    global _last_run_at, _last_result, _last_error
    while not _stop.is_set():
        try:
            with SessionLocal() as db:
                _last_result = run_due_rules(db)
            _last_error = None
        except Exception as exc:
            _last_result = None
            _last_error = exc.__class__.__name__
            log.exception("AI Secretary automation pass failed")
        _last_run_at = datetime.now(timezone.utc).isoformat()
        _stop.wait(interval_seconds())


def start() -> bool:
    global _thread
    if not enabled() or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = Thread(target=_worker, name="ai-secretary-automation", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)


def status() -> dict:
    return {
        "enabled": enabled(), "running": bool(_thread and _thread.is_alive()),
        "interval_seconds": interval_seconds(), "last_run_at": _last_run_at,
        "last_result": dict(_last_result) if _last_result else None, "last_error": _last_error,
    }
