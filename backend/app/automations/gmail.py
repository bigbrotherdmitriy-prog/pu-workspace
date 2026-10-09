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
from app.models.project import Project
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


def background_sweep_enabled() -> bool:
    """Require a separate opt-in before scanning every connected project."""
    return enabled() and os.getenv(
        "GMAIL_BACKGROUND_SWEEP_ENABLED", "false",
    ).strip().lower() in {"1", "true", "yes", "on"}


def interval_seconds() -> int:
    return max(60, int(os.getenv("GMAIL_AUTO_SYNC_INTERVAL_SECONDS", "300")))


def _resolve_shared_sync_token_id(db, project_id: int) -> int | None:
    """Resolve the shared Google account's CURRENT live token for a project
    whose own `GoogleOAuthToken` has EVER been part of a mailbox identity's
    credential history (ADR-V6-09, corrected).

    `sync_authorized_projects_once` otherwise authenticates each project with
    its OWN static `GoogleOAuthToken` row. That row is silently invalidated by
    Google whenever ANY other project sharing the same underlying account
    reconnects -- confirmed live in production (projects 14/16/24 broke the
    moment project 17 reconnected; see
    docs/architecture/ADR-V6-09-DUAL-GMAIL-SYNC-RU.md).

    The first cut of this fix gated eligibility on an `enabled=True`
    `MailboxProjectCohort` row. That was wrong: the three projects that
    actually broke (14, 16, 24) only ever have `enabled=False` cohort rows --
    the auto-created-disabled rows `bind_verified_google_subject` writes for
    whichever project owned the token at each past rotation (ADR-V6-08's
    "Авто-создание MailboxProjectCohort"). They were never pilot-enrolled,
    just historically touched the shared account, so the old `enabled=True`
    filter made this resolver return None for exactly the projects it exists
    to fix, leaving them to keep failing with `RefreshError: invalid_grant`.

    The real eligibility test -- mirroring the `mapped = ...` lookup
    `runtime_for_project_connection` (app/mailbox_identity/runtime.py) already
    does for "was this project's own token ever used for this identity" -- is
    simply: does this project's own token map, via
    `MailboxCredentialGeneration.google_token_id`, to a `ConnectionIdentity`
    whose CURRENT generation now points at a token? If so, and that current
    token differs from this project's own, the scheduled plain-read sweep
    should use the current one instead of the stale one. `MailboxProjectCohort`
    (enabled or not) does not gate this -- it is a separate, pilot-provenance
    policy question (`runtime_for_project_connection`'s subsequent
    `_deny()`/cohort-enabled checks, which this plain read sync does not need
    to inherit: this is not pilot shadow/primary-read/actions automation,
    just "which token keeps a plain read sync from immediately failing").

    Returns None for:
    - a project whose own token has never been mapped to any
      `MailboxCredentialGeneration` row at all (the 6 independent/unrelated
      `GoogleOAuthToken` rows with no mailbox_identity generation) -- it has
      never touched any shared identity, so it keeps behaving exactly as
      before this ADR, unaffected.
    - a project whose own token IS still the identity's current generation's
      token (nothing to resolve -- its own token is already the live one).
    """
    from app.models.mailbox_identity import MailboxCredentialGeneration
    from app.models.v54_pilot import ConnectionIdentity

    token = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project_id))
    if not token:
        return None
    # Mirrors runtime_for_project_connection's "mapped = ..." step
    # (app/mailbox_identity/runtime.py:167): has this project's own token EVER
    # been part of a mailbox identity's credential history, at any generation?
    mapped = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.google_token_id == token.id).order_by(
        MailboxCredentialGeneration.generation.desc()))
    if not mapped:
        return None
    identity = db.scalar(select(ConnectionIdentity).where(
        ConnectionIdentity.id == mapped.connection_identity_id,
        ConnectionIdentity.organization_id == mapped.organization_id,
        ConnectionIdentity.state == "verified",
    ))
    if identity is None:
        return None
    generation = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.organization_id == identity.organization_id,
        MailboxCredentialGeneration.connection_identity_id == identity.id,
        MailboxCredentialGeneration.generation == identity.credential_generation,
        MailboxCredentialGeneration.binding_epoch == identity.binding_epoch,
        MailboxCredentialGeneration.state == "active",
    ))
    if generation is None or generation.google_token_id is None or generation.google_token_id == token.id:
        return None
    return generation.google_token_id


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
        acquired = False
        try:
            acquired = bool(connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": _ADVISORY_LOCK_ID},
            ).scalar())
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
            # Archived projects keep their OAuth row (archiving a project does not
            # revoke or delete its Gmail connection) but must never be synced:
            # an archived project has no operator confirming context/tasks for
            # it, so mail routed there would silently pile up unreviewed. See
            # docs/audits/... incident notes: a connection left pointed at an
            # archived-adjacent project was the proximate cause of a week of
            # misrouted client mail.
            project_ids = list(db.scalars(
                select(GoogleOAuthToken.project_id)
                .join(Project, Project.id == GoogleOAuthToken.project_id)
                .where(Project.archived_at.is_(None))
                .order_by(GoogleOAuthToken.project_id)
            ))
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
                    sync_kwargs = {"query": "is:inbox newer_than:7d", "max_results": 25}
                    shared_token_id = _resolve_shared_sync_token_id(db, project_id)
                    if shared_token_id is not None:
                        sync_kwargs["credential_token_id"] = shared_token_id
                    result = sync_gmail_project(project_id, db, user, **sync_kwargs)
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
