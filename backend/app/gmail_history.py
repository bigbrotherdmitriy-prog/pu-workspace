"""Durable Gmail users.history checkpoint on the existing BackgroundJob queue."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.auth import require_project_role
from app.integrations.google_retry import GoogleReadError, execute_google_read
from app.mailbox_identity.runtime import (
    require_mailbox_authority,
    runtime_for_project_connection,
)
from app.models.job import BackgroundJob
from app.models.mailbox_identity import (
    GmailHistoryCheckpoint,
    GmailHistoryCheckpointEvent,
)
from app.models.user import User


MAX_HISTORY_PAGES = 20
MAX_HISTORY_MESSAGES = 100
RESYNC_PAGE_SIZE = 100
MAX_RESYNC_PAGES = 100
MAX_RESYNC_MESSAGES = 10_000


class GmailHistoryUnavailable(RuntimeError):
    """Content-free fail-closed history error."""


class GmailHistoryBusy(GmailHistoryUnavailable):
    """Another durable job owns this checkpoint."""


def _valid_history_id(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 200 and value.isdecimal()


def _event(
    db: Session,
    checkpoint: GmailHistoryCheckpoint,
    *,
    from_epoch: int,
    to_epoch: int,
    outcome: str,
    job_id: int | None,
) -> None:
    db.add(GmailHistoryCheckpointEvent(
        organization_id=checkpoint.organization_id,
        checkpoint_id=checkpoint.id,
        from_epoch=from_epoch,
        to_epoch=to_epoch,
        outcome_code=outcome,
        job_id=job_id,
    ))


def _runtime(db: Session, checkpoint: GmailHistoryCheckpoint):
    db.expire_all()
    checkpoint = db.get(GmailHistoryCheckpoint, checkpoint.id)
    actor = db.get(User, checkpoint.created_by_user_id) if checkpoint else None
    try:
        runtime = runtime_for_project_connection(db, checkpoint.project_id) if checkpoint else None
    except ValueError as exc:
        raise GmailHistoryUnavailable("history_scope_unavailable") from exc
    if (
        checkpoint is None
        or actor is None
        or runtime is None
        or runtime.organization_id != checkpoint.organization_id
        or runtime.identity_id != checkpoint.identity_id
        or runtime.mail_connection_id != checkpoint.mail_connection_id
        or runtime.generation != checkpoint.credential_generation
        or runtime.binding_epoch != checkpoint.binding_epoch
        or not runtime.flags.pilot_write
    ):
        raise GmailHistoryUnavailable("history_scope_unavailable")
    try:
        require_project_role(db, actor, checkpoint.project_id, "editor")
        require_mailbox_authority(
            db, runtime=runtime, actor=actor, permission="ingest",
        )
    except Exception as exc:
        raise GmailHistoryUnavailable("history_scope_unavailable") from exc
    return checkpoint, actor, runtime


def ensure_history_checkpoint(
    db: Session,
    *,
    project_id: int,
    actor: User,
    initial_history_id: str | None = None,
) -> GmailHistoryCheckpoint:
    """Create/reuse the only checkpoint for an exact mailbox identity."""
    if initial_history_id is not None and not _valid_history_id(initial_history_id):
        raise GmailHistoryUnavailable("history_cursor_unavailable")
    require_project_role(db, actor, project_id, "editor")
    try:
        runtime = runtime_for_project_connection(db, project_id)
    except ValueError as exc:
        raise GmailHistoryUnavailable("history_scope_unavailable") from exc
    if runtime is None or not runtime.flags.pilot_write:
        raise GmailHistoryUnavailable("history_scope_unavailable")
    try:
        require_mailbox_authority(db, runtime=runtime, actor=actor, permission="ingest")
    except Exception as exc:
        raise GmailHistoryUnavailable("history_scope_unavailable") from exc
    existing = db.scalar(select(GmailHistoryCheckpoint).where(
        GmailHistoryCheckpoint.organization_id == runtime.organization_id,
        GmailHistoryCheckpoint.mail_connection_id == runtime.mail_connection_id,
    ).with_for_update())
    if existing is not None:
        if (
            existing.identity_id != runtime.identity_id
        ):
            raise GmailHistoryUnavailable("history_scope_unavailable")
        require_project_role(db, actor, existing.project_id, "editor")
        if (
            existing.credential_generation != runtime.generation
            or existing.binding_epoch != runtime.binding_epoch
        ):
            if (
                existing.status == "syncing"
                or runtime.generation <= existing.credential_generation
                or runtime.binding_epoch < existing.binding_epoch
            ):
                raise GmailHistoryUnavailable("history_scope_unavailable")
            old_epoch = existing.checkpoint_epoch
            rotated = db.execute(update(GmailHistoryCheckpoint).where(
                GmailHistoryCheckpoint.id == existing.id,
                GmailHistoryCheckpoint.checkpoint_epoch == old_epoch,
                GmailHistoryCheckpoint.status != "syncing",
            ).values(
                credential_generation=runtime.generation,
                binding_epoch=runtime.binding_epoch,
                last_history_id=None,
                status="resync_required",
                active_job_id=None,
                sync_mode=None,
                checkpoint_epoch=old_epoch + 1,
                updated_at=datetime.now(timezone.utc),
            ).execution_options(synchronize_session=False))
            if rotated.rowcount != 1:
                db.rollback()
                raise GmailHistoryBusy("history_checkpoint_busy")
            db.expire_all()
            existing = db.get(GmailHistoryCheckpoint, existing.id)
            _event(
                db, existing, from_epoch=old_epoch, to_epoch=old_epoch + 1,
                outcome="generation_rotated", job_id=None,
            )
            db.commit()
            db.refresh(existing)
        return existing
    checkpoint = GmailHistoryCheckpoint(
        organization_id=runtime.organization_id,
        project_id=project_id,
        identity_id=runtime.identity_id,
        mail_connection_id=runtime.mail_connection_id,
        credential_generation=runtime.generation,
        binding_epoch=runtime.binding_epoch,
        created_by_user_id=actor.id,
        last_history_id=initial_history_id,
        status="active" if initial_history_id is not None else "resync_required",
        checkpoint_epoch=1,
    )
    db.add(checkpoint)
    db.commit()
    db.refresh(checkpoint)
    return checkpoint


def enqueue_history_sync(db: Session, checkpoint_id: str) -> BackgroundJob:
    """Enqueue by opaque ID only; the mutable epoch scopes idempotency."""
    from app.jobs.queue import enqueue

    checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
    if checkpoint is None or checkpoint.status == "blocked":
        raise GmailHistoryUnavailable("history_checkpoint_unavailable")
    if checkpoint.status == "syncing" and checkpoint.active_job_id is not None:
        existing = db.get(BackgroundJob, checkpoint.active_job_id)
        if existing is not None:
            return existing
        raise GmailHistoryBusy("history_checkpoint_busy")
    return enqueue(
        db,
        "gmail.history.sync",
        {"checkpoint_id": checkpoint.id},
        idempotency_key=f"gmail-history:{checkpoint.id}:{checkpoint.checkpoint_epoch}",
        max_attempts=5,
    )


def _message_id(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    message_id = value.get("id")
    return message_id if isinstance(message_id, str) and 0 < len(message_id) <= 500 else None


def _history_messages(page: dict) -> list[str]:
    history = page.get("history") or []
    if not isinstance(history, list):
        raise GmailHistoryUnavailable("history_page_unavailable")
    result: list[str] = []
    for record in history:
        if not isinstance(record, dict):
            raise GmailHistoryUnavailable("history_page_unavailable")
        candidates = record.get("messages") or []
        added = record.get("messagesAdded") or []
        if not isinstance(candidates, list) or not isinstance(added, list):
            raise GmailHistoryUnavailable("history_page_unavailable")
        for value in candidates:
            message_id = _message_id(value)
            if message_id is None:
                raise GmailHistoryUnavailable("history_page_unavailable")
            result.append(message_id)
        for value in added:
            if not isinstance(value, dict):
                raise GmailHistoryUnavailable("history_page_unavailable")
            message_id = _message_id(value.get("message"))
            if message_id is None:
                raise GmailHistoryUnavailable("history_page_unavailable")
            result.append(message_id)
    return result


def _incremental(
    service,
    start_history_id: str,
    *,
    before_attempt: Callable[[], None],
) -> tuple[list[dict[str, str]], str]:
    page_token: str | None = None
    seen_tokens: set[str] = set()
    seen_messages: set[str] = set()
    refs: list[dict[str, str]] = []
    final_history_id: str | None = None
    for _page_number in range(MAX_HISTORY_PAGES):
        request = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "maxResults": MAX_HISTORY_MESSAGES,
        }
        if page_token is not None:
            request["pageToken"] = page_token
        page = execute_google_read(
            lambda request=request: service.users().history().list(**request),
            before_attempt=before_attempt,
        )
        if not isinstance(page, dict):
            raise GmailHistoryUnavailable("history_page_unavailable")
        for message_id in _history_messages(page):
            if message_id not in seen_messages:
                if len(refs) >= MAX_HISTORY_MESSAGES:
                    raise GmailHistoryUnavailable("history_page_limit")
                seen_messages.add(message_id)
                refs.append({"id": message_id})
        next_token = page.get("nextPageToken")
        if next_token is None:
            final_history_id = page.get("historyId")
            break
        if (
            not isinstance(next_token, str)
            or not next_token
            or len(next_token) > 2000
            or next_token in seen_tokens
        ):
            raise GmailHistoryUnavailable("history_page_unavailable")
        seen_tokens.add(next_token)
        page_token = next_token
    else:
        raise GmailHistoryUnavailable("history_page_limit")
    if not _valid_history_id(final_history_id):
        raise GmailHistoryUnavailable("history_cursor_unavailable")
    return refs, final_history_id


def _safe_result(result: object, mode: str) -> dict:
    safe: dict[str, int | bool | str] = {"mode": mode}
    if isinstance(result, dict):
        for key in ("processed", "skipped", "failed", "deduplicated", "overlap_skipped"):
            value = result.get(key)
            if type(value) in (int, bool):
                safe[key] = value
    return safe


def _accepted_result(result: object) -> bool:
    return (
        isinstance(result, dict)
        and type(result.get("failed", 0)) is int
        and result.get("failed", 0) == 0
    )


def _terminal_replay(db: Session, checkpoint: GmailHistoryCheckpoint, job_id: int):
    if checkpoint.last_job_id != job_id or not isinstance(checkpoint.last_result, dict):
        return None
    completed = db.scalar(select(GmailHistoryCheckpointEvent.id).where(
        GmailHistoryCheckpointEvent.checkpoint_id == checkpoint.id,
        GmailHistoryCheckpointEvent.job_id == job_id,
        GmailHistoryCheckpointEvent.outcome_code.in_(("sync_completed", "resync_completed")),
    ).limit(1))
    return dict(checkpoint.last_result) if completed else None


def run_history_sync(
    db: Session,
    *,
    checkpoint_id: str,
    job_id: int,
    service,
    process_refs: Callable[[list[dict[str, str]]], dict],
    full_resync: Callable[[], tuple[str, dict]],
    execution_guard: Callable[[], None] | None = None,
    bind_guard: Callable[[Callable[[], None]], None] | None = None,
) -> dict:
    """Claim, read and CAS-advance one checkpoint; safe for lease replay."""
    checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
    if execution_guard is not None:
        execution_guard()
    if checkpoint is None or type(job_id) is not int or job_id <= 0:
        raise GmailHistoryUnavailable("history_checkpoint_unavailable")
    replay = _terminal_replay(db, checkpoint, job_id)
    if replay is not None:
        return replay
    try:
        checkpoint, _actor, _mailbox = _runtime(db, checkpoint)
    except GmailHistoryUnavailable:
        epoch = checkpoint.checkpoint_epoch
        _event(db, checkpoint, from_epoch=epoch, to_epoch=epoch,
               outcome="generation_rejected", job_id=job_id)
        db.commit()
        raise
    if checkpoint.status == "syncing":
        if checkpoint.active_job_id != job_id:
            raise GmailHistoryBusy("history_checkpoint_busy")
        mode = checkpoint.sync_mode
        claimed_epoch = checkpoint.checkpoint_epoch
    elif checkpoint.status in ("active", "resync_required"):
        mode = "incremental" if checkpoint.status == "active" and checkpoint.last_history_id else "resync"
        old_epoch = checkpoint.checkpoint_epoch
        claimed_epoch = old_epoch + 1
        claimed = db.execute(update(GmailHistoryCheckpoint).where(
            GmailHistoryCheckpoint.id == checkpoint.id,
            GmailHistoryCheckpoint.checkpoint_epoch == old_epoch,
            GmailHistoryCheckpoint.status == checkpoint.status,
        ).values(
            status="syncing",
            sync_mode=mode,
            active_job_id=job_id,
            checkpoint_epoch=claimed_epoch,
            updated_at=datetime.now(timezone.utc),
        ).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            db.rollback()
            raise GmailHistoryBusy("history_checkpoint_busy")
        db.expire_all()
        checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
        _event(db, checkpoint, from_epoch=old_epoch, to_epoch=claimed_epoch,
               outcome="sync_claimed", job_id=job_id)
        db.commit()
    else:
        raise GmailHistoryUnavailable("history_checkpoint_unavailable")

    def current_guard():
        if execution_guard is not None:
            execution_guard()
        db.expire_all()
        current = db.get(GmailHistoryCheckpoint, checkpoint_id)
        if (
            current is None
            or current.status != "syncing"
            or current.active_job_id != job_id
            or current.checkpoint_epoch != claimed_epoch
        ):
            raise GmailHistoryBusy("history_checkpoint_busy")
        _runtime(db, current)

    try:
        # Worker callbacks must use this exact claim/epoch fence, including the
        # updated epoch when an expired incremental cursor enters resync.
        if bind_guard is not None:
            bind_guard(current_guard)
        if mode == "incremental":
            try:
                refs, history_id = _incremental(
                    service, checkpoint.last_history_id,
                    before_attempt=current_guard,
                )
                current_guard()
                result = process_refs(refs)
            except GoogleReadError as exc:
                if exc.status != 404:
                    raise GmailHistoryUnavailable("history_provider_unavailable") from exc
                old_epoch = checkpoint.checkpoint_epoch
                claimed_epoch = old_epoch + 1
                transitioned = db.execute(update(GmailHistoryCheckpoint).where(
                    GmailHistoryCheckpoint.id == checkpoint_id,
                    GmailHistoryCheckpoint.status == "syncing",
                    GmailHistoryCheckpoint.active_job_id == job_id,
                    GmailHistoryCheckpoint.checkpoint_epoch == old_epoch,
                ).values(
                    sync_mode="resync",
                    checkpoint_epoch=claimed_epoch,
                    updated_at=datetime.now(timezone.utc),
                ).execution_options(synchronize_session=False))
                if transitioned.rowcount != 1:
                    db.rollback()
                    raise GmailHistoryBusy("history_checkpoint_busy")
                db.expire_all()
                checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
                _event(db, checkpoint, from_epoch=old_epoch, to_epoch=claimed_epoch,
                       outcome="cursor_expired", job_id=job_id)
                db.commit()
                mode = "resync"
        if mode == "resync":
            current_guard()
            history_id, result = full_resync()
            if not _valid_history_id(history_id):
                raise GmailHistoryUnavailable("history_cursor_unavailable")
        if not _accepted_result(result):
            raise GmailHistoryUnavailable("history_ingest_incomplete")
        current_guard()
        checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
        old_epoch = checkpoint.checkpoint_epoch
        next_epoch = old_epoch + 1
        safe_result = _safe_result(result, mode)
        completed = db.execute(update(GmailHistoryCheckpoint).where(
            GmailHistoryCheckpoint.id == checkpoint_id,
            GmailHistoryCheckpoint.status == "syncing",
            GmailHistoryCheckpoint.active_job_id == job_id,
            GmailHistoryCheckpoint.checkpoint_epoch == old_epoch,
        ).values(
            last_history_id=history_id,
            status="active",
            sync_mode=None,
            active_job_id=None,
            last_job_id=job_id,
            last_success_at=datetime.now(timezone.utc),
            checkpoint_epoch=next_epoch,
            last_result=safe_result,
            updated_at=datetime.now(timezone.utc),
        ).execution_options(synchronize_session=False))
        if completed.rowcount != 1:
            db.rollback()
            raise GmailHistoryBusy("history_checkpoint_busy")
        db.expire_all()
        checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
        outcome = "resync_completed" if mode == "resync" else "sync_completed"
        _event(db, checkpoint, from_epoch=old_epoch,
               to_epoch=next_epoch, outcome=outcome, job_id=job_id)
        db.commit()
        return safe_result
    except Exception as exc:
        db.rollback()
        checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
        if (
            checkpoint is not None
            and checkpoint.status == "syncing"
            and checkpoint.active_job_id == job_id
            and checkpoint.checkpoint_epoch == claimed_epoch
        ):
            if execution_guard is not None:
                execution_guard()
            old_epoch = checkpoint.checkpoint_epoch
            next_epoch = old_epoch + 1
            released = db.execute(update(GmailHistoryCheckpoint).where(
                GmailHistoryCheckpoint.id == checkpoint_id,
                GmailHistoryCheckpoint.status == "syncing",
                GmailHistoryCheckpoint.active_job_id == job_id,
                GmailHistoryCheckpoint.checkpoint_epoch == old_epoch,
            ).values(
                status="resync_required" if checkpoint.sync_mode == "resync" else "active",
                sync_mode=None,
                active_job_id=None,
                checkpoint_epoch=next_epoch,
                updated_at=datetime.now(timezone.utc),
            ).execution_options(synchronize_session=False))
            if released.rowcount != 1:
                db.rollback()
                raise GmailHistoryBusy("history_checkpoint_busy") from exc
            db.expire_all()
            checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
            _event(db, checkpoint, from_epoch=old_epoch,
                   to_epoch=next_epoch,
                   outcome="sync_failed", job_id=job_id)
            db.commit()
        if isinstance(exc, GmailHistoryUnavailable):
            raise
        raise GmailHistoryUnavailable("history_sync_unavailable") from exc


def run_gmail_history_job(payload: dict) -> dict:
    """Production worker boundary; payload contains only the opaque checkpoint ID."""
    from app.api.gmail import sync_gmail_project
    from app.database import SessionLocal
    from app.integrations.google_workspace import google_workspace_for_mailbox
    from app.jobs.queue import current_execution_claim

    if not isinstance(payload, dict) or set(payload) != {"checkpoint_id"}:
        raise GmailHistoryUnavailable("history_job_unavailable")
    checkpoint_id = payload.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise GmailHistoryUnavailable("history_job_unavailable")
    claim = current_execution_claim()
    if claim is None:
        raise GmailHistoryUnavailable("history_job_unavailable")
    job_id = claim[0]
    with SessionLocal() as db:
        def execution_guard():
            require_history_execution_claim(db, claim)

        execution_guard()
        checkpoint = db.get(GmailHistoryCheckpoint, checkpoint_id)
        if checkpoint is None:
            raise GmailHistoryUnavailable("history_checkpoint_unavailable")
        checkpoint, actor, runtime = _runtime(db, checkpoint)
        service = google_workspace_for_mailbox(
            runtime.google_token_id, db,
        ).service("gmail", "v1")

        checkpoint_guard = None

        def bind_guard(guard):
            nonlocal checkpoint_guard
            checkpoint_guard = guard

        def provider_guard():
            if checkpoint_guard is None:
                raise GmailHistoryBusy("history_checkpoint_busy")
            checkpoint_guard()

        def process_refs(refs):
            return sync_gmail_project(
                checkpoint.project_id, db, actor,
                query="history", max_results=MAX_HISTORY_MESSAGES,
                _service=service, _refs=refs, _before_read=provider_guard,
            )

        def full_resync():
            return bounded_history_resync(service, process_refs, provider_guard)

        return run_history_sync(
            db,
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            service=service,
            process_refs=process_refs,
            full_resync=full_resync,
            execution_guard=execution_guard,
            bind_guard=bind_guard,
        )


def require_history_execution_claim(db: Session, claim: tuple) -> None:
    """Fence the exact worker attempt, including lease expiry and reuse of its ID."""
    job_id, worker_id, attempt, locked_at = claim
    live = db.scalar(select(BackgroundJob.id).where(
        BackgroundJob.id == job_id,
        BackgroundJob.status == "running",
        BackgroundJob.worker_id == worker_id,
        BackgroundJob.attempts == attempt,
        BackgroundJob.locked_at == locked_at,
        BackgroundJob.lease_expires_at > datetime.now(timezone.utc),
    ))
    if live is None:
        raise GmailHistoryBusy("history_execution_unavailable")


def bounded_history_resync(service, process_refs, before_read):
    """Ingest one bounded page at a time; restart failures from the first page.

    The pre-scan history pin is returned only on complete ingestion. No provider
    continuation is persisted: existing mailbox message identity deduplicates
    successfully committed rows on replay, without needing another schema.
    """
    from app.api.gmail import _gmail_resync_pages

    profile = execute_google_read(
        lambda: service.users().getProfile(userId="me"), before_attempt=before_read,
    )
    history_id = profile.get("historyId") if isinstance(profile, dict) else None
    if not _valid_history_id(history_id):
        raise GmailHistoryUnavailable("history_cursor_unavailable")
    totals = {}
    for refs in _gmail_resync_pages(
        service, query="is:inbox newer_than:30d", page_size=RESYNC_PAGE_SIZE,
        max_pages=MAX_RESYNC_PAGES, max_messages=MAX_RESYNC_MESSAGES,
        before_attempt=before_read,
    ):
        before_read()
        result = process_refs(refs) if refs else {"processed": 0}
        if not _accepted_result(result):
            raise GmailHistoryUnavailable("history_ingest_incomplete")
        for key, count in _safe_result(result, "resync").items():
            if type(count) in (int, bool):
                totals[key] = totals.get(key, 0) + int(count)
        before_read()
    before_read()
    return history_id, totals
