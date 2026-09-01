from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.job import BackgroundJob, ServiceHeartbeat

READY_STATUSES = ("queued", "retrying")
TERMINAL_STATUSES = ("failed", "dead_letter", "completed", "cancelled")

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def safe_error(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        name = error.__class__.__name__
        message = str(error) if isinstance(error, (ValueError, TimeoutError, ConnectionError)) else ""
    else:
        name, message = "JobError", str(error)
    message = re.sub(r"(?i)(token|secret|password|authorization|api[_-]?key)\s*[:=]\s*\S+", r"\1=[REDACTED]", message)
    message = re.sub(r"https?://\S+", "[URL REDACTED]", message)
    message = " ".join(message.split())[:500]
    return f"{name}: {message}" if message else name

def touch_service(db: Session, service_id: str, service_kind: str, metadata: dict | None = None) -> None:
    now = utcnow()
    row = db.get(ServiceHeartbeat, service_id)
    if row is None:
        db.add(ServiceHeartbeat(service_id=service_id, service_kind=service_kind, last_seen=now, metadata_json=metadata or {}))
    else:
        row.last_seen = now
        row.metadata_json = metadata or row.metadata_json
    db.commit()

def enqueue(db: Session, kind: str, payload: dict[str, Any], *, priority: int = 100,
            max_attempts: int = 3, idempotency_key: str | None = None,
            available_at: datetime | None = None) -> BackgroundJob:
    if idempotency_key:
        existing = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    job = BackgroundJob(kind=kind, payload=payload, priority=priority, max_attempts=max_attempts,
                        idempotency_key=idempotency_key, available_at=available_at or utcnow(), progress=0)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if not idempotency_key:
            raise
        existing = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
    db.refresh(job)
    return job

def recover_expired(db: Session) -> int:
    if db.bind is not None and db.bind.dialect.name != "postgresql":
        rows = list(db.scalars(select(BackgroundJob).where(BackgroundJob.status == "running", BackgroundJob.lease_expires_at < utcnow()).with_for_update()))
        for job in rows:
            job.status = "dead_letter" if job.attempts >= job.max_attempts else "retrying"
            job.available_at = utcnow(); job.worker_id = job.locked_at = job.lease_expires_at = None
            job.last_error = "Worker lease expired; job recovered."
        db.commit()
        return len(rows)
    result = db.execute(text("""
        UPDATE background_jobs
        SET status=CASE WHEN attempts >= max_attempts THEN 'dead_letter' ELSE 'retrying' END,
            available_at=now(), worker_id=NULL, locked_at=NULL, lease_expires_at=NULL,
            last_error='Worker lease expired; job recovered.', updated_at=now()
        WHERE status='running' AND lease_expires_at < now()
    """))
    db.commit()
    return result.rowcount or 0

def claim(db: Session, worker_id: str, lease_seconds: int = 300) -> BackgroundJob | None:
    now = utcnow(); lease_until = now + timedelta(seconds=lease_seconds)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        job_id = db.execute(text("""
            WITH candidate AS (
                SELECT id FROM background_jobs
                WHERE status IN ('queued','retrying') AND available_at <= now()
                ORDER BY priority ASC, id ASC FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE background_jobs AS job
            SET status='running', attempts=attempts+1, worker_id=:worker_id,
                locked_at=now(), started_at=COALESCE(started_at, now()),
                lease_expires_at=:lease_until, progress=GREATEST(progress, 1), updated_at=now()
            FROM candidate WHERE job.id=candidate.id RETURNING job.id
        """), {"worker_id": worker_id, "lease_until": lease_until}).scalar()
        db.commit()
        return db.get(BackgroundJob, job_id) if job_id is not None else None
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.status.in_(READY_STATUSES), BackgroundJob.available_at <= now).with_for_update().order_by(BackgroundJob.priority, BackgroundJob.id).limit(1))
    if job is None:
        return None
    job.status, job.attempts, job.worker_id = "running", job.attempts + 1, worker_id
    job.locked_at, job.lease_expires_at, job.started_at = now, lease_until, job.started_at or now
    job.progress = max(job.progress or 0, 1)
    db.commit(); db.refresh(job)
    return job

def heartbeat(db: Session, job_id: int, worker_id: str, lease_seconds: int = 300) -> bool:
    owner_id = worker_id
    result = db.execute(update(BackgroundJob).where(BackgroundJob.id == job_id, BackgroundJob.status == "running", BackgroundJob.worker_id == owner_id).values(lease_expires_at=utcnow() + timedelta(seconds=lease_seconds), updated_at=utcnow()))
    db.commit(); return bool(result.rowcount)

def set_progress(db: Session, job_id: int, worker_id: str, progress: int) -> bool:
    owner_id = worker_id
    result = db.execute(update(BackgroundJob).where(BackgroundJob.id == job_id, BackgroundJob.status == "running", BackgroundJob.worker_id == owner_id).values(progress=max(1, min(99, int(progress))), updated_at=utcnow()))
    db.commit(); return bool(result.rowcount)

def update_cooperative_progress(
    db: Session, job_id: int, progress: int, detail: dict[str, Any] | None = None,
) -> tuple[bool, bool]:
    """Update progress from a running handler and return (updated, cancel_requested)."""
    job = db.scalar(select(BackgroundJob).where(
        BackgroundJob.id == job_id, BackgroundJob.status == "running",
    ).with_for_update())
    if job is None:
        return False, True
    current = dict(job.result or {})
    cancel_requested = bool(current.get("cancel_requested"))
    job.progress = max(1, min(99, int(progress)))
    job.result = {**current, "progress": detail or {"percent": job.progress},
                  "cancel_requested": cancel_requested}
    db.commit()
    return True, cancel_requested

def request_cancel(db: Session, job_id: int, *, allow_running: bool = False) -> str | None:
    """Cancel waiting work or request cooperative cancellation of a running job."""
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update())
    if job is None:
        return None
    if job.status in READY_STATUSES:
        ended = utcnow()
        job.status, job.cancelled_at, job.completed_at = "cancelled", ended, ended
        job.result = {**dict(job.result or {}), "cancelled": True}
        job.duration_ms = _duration(job, ended)
        db.commit()
        return "cancelled"
    if allow_running and job.status == "running":
        job.result = {**dict(job.result or {}), "cancel_requested": True}
        db.commit()
        return "cancellation_requested"
    return job.status

def _duration(job: BackgroundJob, ended: datetime) -> int | None:
    if not job.started_at:
        return None
    started = job.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    return max(0, int((ended - started).total_seconds() * 1000))

def succeed(db: Session, job_id: int, worker_id: str, result: dict | None = None) -> bool:
    owner_id = worker_id
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id, BackgroundJob.status == "running", BackgroundJob.worker_id == owner_id).with_for_update())
    if job is None:
        return False
    ended = utcnow()
    cancelled = bool((result or {}).get("cancelled"))
    job.status, job.result = ("cancelled" if cancelled else "completed"), (result or {})
    job.progress = min(99, job.progress or 0) if cancelled else 100
    if cancelled:
        job.cancelled_at = ended
    job.completed_at, job.duration_ms, job.lease_expires_at = ended, _duration(job, ended), None
    db.commit(); return True

def fail(db: Session, job_id: int, worker_id: str, error: BaseException | str, *, retryable: bool = True) -> str:
    owner_id = worker_id
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id, BackgroundJob.status == "running", BackgroundJob.worker_id == owner_id).with_for_update())
    if job is None:
        return "lost"
    terminal = job.attempts >= job.max_attempts
    job.status = "failed" if not retryable else "dead_letter" if terminal else "retrying"
    job.last_error = safe_error(error)
    job.worker_id = job.locked_at = job.lease_expires_at = None
    job.available_at = utcnow() + timedelta(seconds=min(300, 5 * (2 ** max(job.attempts - 1, 0))))
    if job.status in TERMINAL_STATUSES:
        job.completed_at = utcnow(); job.duration_ms = _duration(job, job.completed_at)
    db.commit(); return job.status

def cancel(db: Session, job_id: int) -> bool:
    return request_cancel(db, job_id) == "cancelled"

def retry(db: Session, job_id: int, *, redrive: bool = False) -> bool:
    allowed = ("dead_letter",) if redrive else ("failed",)
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id, BackgroundJob.status.in_(allowed)).with_for_update())
    if job is None:
        return False
    job.status, job.available_at, job.completed_at = "queued", utcnow(), None
    job.worker_id = job.locked_at = job.lease_expires_at = None
    job.last_error, job.progress, job.started_at, job.duration_ms = None, 0, None, None
    if redrive:
        job.attempts = 0
    db.commit(); return True

def metrics(db: Session, heartbeat_seconds: int = 90) -> dict:
    now = utcnow()
    counts = dict(db.execute(select(BackgroundJob.status, func.count()).group_by(BackgroundJob.status)).all())
    oldest = db.scalar(select(func.min(BackgroundJob.created_at)).where(BackgroundJob.status.in_(READY_STATUSES)))
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    workers = db.scalar(select(func.count()).select_from(ServiceHeartbeat).where(ServiceHeartbeat.service_kind == "worker", ServiceHeartbeat.last_seen >= now - timedelta(seconds=heartbeat_seconds))) or 0
    return {"queue_length": sum(int(counts.get(s, 0)) for s in READY_STATUSES), "oldest_job_age_seconds": max(0, int((now - oldest).total_seconds())) if oldest else 0, "errors": int(counts.get("failed", 0)) + int(counts.get("dead_letter", 0)), "workers": int(workers), "statuses": {str(k): int(v) for k, v in counts.items()}}
