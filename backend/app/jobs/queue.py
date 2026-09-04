from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.job import BackgroundJob, ServiceHeartbeat


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def touch_service(db: Session, service_id: str, service_kind: str, metadata: dict | None = None) -> None:
    row = db.get(ServiceHeartbeat, service_id)
    if row is None:
        row = ServiceHeartbeat(
            service_id=service_id, service_kind=service_kind,
            last_seen=utcnow(), metadata_json=metadata or {},
        )
        db.add(row)
    else:
        row.last_seen = utcnow()
        row.metadata_json = metadata or row.metadata_json
    db.commit()


def enqueue(
    db: Session, kind: str, payload: dict[str, Any], *, priority: int = 100,
    max_attempts: int = 3, idempotency_key: str | None = None,
    available_at: datetime | None = None,
) -> BackgroundJob:
    """Persist work before returning to the caller; duplicate keys are idempotent."""
    if idempotency_key:
        existing = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    job = BackgroundJob(
        kind=kind, payload=payload, priority=priority, max_attempts=max_attempts,
        idempotency_key=idempotency_key, available_at=available_at or utcnow(),
    )
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
    """Return abandoned leased jobs to the queue after a worker crash."""
    now = utcnow()
    rows = list(db.scalars(select(BackgroundJob).where(
        BackgroundJob.status == "running", BackgroundJob.lease_expires_at < now,
    )))
    for job in rows:
        job.status = "dead_letter" if job.attempts >= job.max_attempts else "queued"
        job.available_at = now
        job.worker_id = None
        job.locked_at = None
        job.lease_expires_at = None
        job.last_error = ((job.last_error or "") + "\nWorker lease expired; job recovered.").strip()
    db.commit()
    return len(rows)


def claim(db: Session, worker_id: str, lease_seconds: int = 300) -> BackgroundJob | None:
    """Atomically claim one ready job. PostgreSQL workers never block each other."""
    now = utcnow()
    lease_until = now + timedelta(seconds=lease_seconds)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        job_id = db.execute(text("""
            WITH candidate AS (
                SELECT id FROM background_jobs
                WHERE status = 'queued' AND available_at <= now()
                ORDER BY priority ASC, id ASC
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE background_jobs AS job
            SET status='running', attempts=attempts+1, worker_id=:worker_id,
                locked_at=now(), lease_expires_at=:lease_until, updated_at=now()
            FROM candidate WHERE job.id=candidate.id RETURNING job.id
        """), {"worker_id": worker_id, "lease_until": lease_until}).scalar()
        db.commit()
        return db.get(BackgroundJob, job_id) if job_id is not None else None
    job = db.scalar(select(BackgroundJob).where(
        BackgroundJob.status == "queued", BackgroundJob.available_at <= now,
    ).order_by(BackgroundJob.priority, BackgroundJob.id).limit(1))
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.worker_id = worker_id
    job.locked_at = now
    job.lease_expires_at = lease_until
    db.commit()
    db.refresh(job)
    return job


def heartbeat(db: Session, job_id: int, worker_id: str, lease_seconds: int = 300) -> bool:
    job = db.get(BackgroundJob, job_id)
    if job is None or job.status != "running" or job.worker_id != worker_id:
        return False
    job.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
    db.commit()
    return True


def succeed(db: Session, job_id: int, worker_id: str, result: dict | None = None) -> bool:
    job = db.get(BackgroundJob, job_id)
    if job is None or job.status != "running" or job.worker_id != worker_id:
        return False
    job.status = "succeeded"
    job.result = result or {}
    job.completed_at = utcnow()
    job.lease_expires_at = None
    db.commit()
    return True


def fail(db: Session, job_id: int, worker_id: str, error: str) -> str:
    job = db.get(BackgroundJob, job_id)
    if job is None or job.status != "running" or job.worker_id != worker_id:
        return "lost"
    terminal = job.attempts >= job.max_attempts
    job.status = "dead_letter" if terminal else "queued"
    job.last_error = error[:4000]
    job.worker_id = None
    job.locked_at = None
    job.lease_expires_at = None
    job.available_at = utcnow() + timedelta(seconds=min(300, 5 * (2 ** max(job.attempts - 1, 0))))
    db.commit()
    return job.status
