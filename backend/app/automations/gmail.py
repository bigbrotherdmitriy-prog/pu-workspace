from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event, Lock, Thread

from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.project_member import ProjectMember
from app.models.user import User

log = logging.getLogger(__name__)
_stop = Event()
_run_lock = Lock()
_state_lock = Lock()
_thread: Thread | None = None
_last_run_at: str | None = None
_last_result: dict[str, int] | None = None
_last_error: str | None = None
_ADVISORY_LOCK_ID = 705_919_301


def enabled() -> bool:
    return os.getenv("GMAIL_AUTO_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def interval_seconds() -> int:
    return max(60, int(os.getenv("GMAIL_AUTO_SYNC_INTERVAL_SECONDS", "300")))


def _automation_user(db, project_id: int) -> User | None:
    role_order = {"owner": 0, "manager": 1, "editor": 2}
    members = db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id, ProjectMember.role.in_(tuple(role_order)))
    ).all()
    if members:
        return min(members, key=lambda pair: role_order[pair[0].role])[1]
    return db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))


@contextmanager
def _exclusive_run():
    """Prevent overlapping passes both inside one process and across PostgreSQL workers."""
    if engine.dialect.name == "postgresql":
        connection = engine.connect()
        acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": _ADVISORY_LOCK_ID}).scalar())
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": _ADVISORY_LOCK_ID})
            connection.close()
        return
    acquired = _run_lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _run_lock.release()


def sync_authorized_projects_once() -> dict[str, int]:
    """Run one bounded pass. A database lock prevents overlap across web workers."""
    with _exclusive_run() as acquired:
        if not acquired:
            return {"projects": 0, "processed": 0, "skipped": 0, "failed": 0, "overlap_skipped": 1}
        totals = {"projects": 0, "processed": 0, "skipped": 0, "failed": 0, "overlap_skipped": 0}
        from app.api.gmail import sync_gmail_project

        with SessionLocal() as db:
            project_ids = list(db.scalars(select(GoogleOAuthToken.project_id).order_by(GoogleOAuthToken.project_id)))
        for project_id in project_ids:
            with SessionLocal() as db:
                try:
                    user = _automation_user(db, project_id)
                    if user is None:
                        totals["failed"] += 1
                        db.add(AuditLog(action="gmail_auto_sync_failed", entity_type="project", entity_id=project_id,
                                        details="reason=no_authorized_actor"))
                        db.commit()
                        continue
                    result = sync_gmail_project(project_id, db, user, query="is:inbox newer_than:7d", max_results=25)
                    totals["projects"] += 1
                    for key in ("processed", "skipped", "failed"):
                        totals[key] += int(result.get(key, 0))
                except Exception as exc:
                    db.rollback()
                    totals["failed"] += 1
                    db.add(AuditLog(action="gmail_auto_sync_failed", entity_type="project", entity_id=project_id,
                                    details=f"error={exc.__class__.__name__}"))
                    db.commit()
                    log.exception("Automatic Gmail synchronization failed for project %s", project_id)
        return totals


def _worker() -> None:
    global _last_run_at, _last_result, _last_error
    while not _stop.is_set():
        try:
            result = sync_authorized_projects_once()
            with _state_lock:
                _last_run_at = datetime.now(timezone.utc).isoformat()
                _last_result = result
                _last_error = None
        except Exception as exc:
            with _state_lock:
                _last_run_at = datetime.now(timezone.utc).isoformat()
                _last_result = None
                _last_error = exc.__class__.__name__
            log.exception("Automatic Gmail synchronization pass failed")
        _stop.wait(interval_seconds())


def start() -> bool:
    global _thread
    if not enabled() or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = Thread(target=_worker, name="gmail-auto-sync", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)


def status() -> dict:
    with _state_lock:
        return {
            "enabled": enabled(),
            "running": bool(_thread and _thread.is_alive()),
            "interval_seconds": interval_seconds(),
            "last_run_at": _last_run_at,
            "last_result": dict(_last_result) if _last_result is not None else None,
            "last_error": _last_error,
            "lock_scope": "database" if engine.dialect.name == "postgresql" else "process",
        }
