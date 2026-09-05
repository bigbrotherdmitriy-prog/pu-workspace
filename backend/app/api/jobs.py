from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.database import get_db
from app.jobs.handlers import notify_outcome
from app.jobs.queue import cancel, metrics, retry
from app.models.job import BackgroundJob, ServiceHeartbeat
from app.models.user import User

router = APIRouter(prefix="/admin/jobs", tags=["background-jobs"], dependencies=[Depends(require_admin)])

def _job(row: BackgroundJob) -> dict:
    return {"id": row.id, "kind": row.kind, "status": row.status, "progress": row.progress,
            "attempts": row.attempts, "max_attempts": row.max_attempts, "worker_id": row.worker_id,
            "duration_ms": row.duration_ms, "error": row.last_error,
            "created_at": row.created_at, "updated_at": row.updated_at}

@router.get("")
def list_jobs(status: str | None = None, limit: int = Query(100, ge=1, le=500),
              db: Session = Depends(get_db), _: User = Depends(require_admin)):
    query = select(BackgroundJob).order_by(BackgroundJob.id.desc()).limit(limit)
    if status:
        query = query.where(BackgroundJob.status == status)
    return {"jobs": [_job(row) for row in db.scalars(query)]}

@router.get("/metrics")
def queue_metrics(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    result = metrics(db)
    result["heartbeats"] = [{"service_id": row.service_id, "kind": row.service_kind, "last_seen": row.last_seen}
                            for row in db.scalars(select(ServiceHeartbeat).order_by(ServiceHeartbeat.last_seen.desc()))]
    return result

@router.post("/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if not cancel(db, job_id):
        raise HTTPException(409, "Only queued or retrying jobs can be cancelled")
    job = db.get(BackgroundJob, job_id)
    notify_outcome(job.kind, dict(job.payload or {}), "cancelled")
    return _job(job)

@router.post("/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if not retry(db, job_id):
        raise HTTPException(409, "Only failed jobs can be retried")
    return _job(db.get(BackgroundJob, job_id))

@router.post("/{job_id}/redrive")
def redrive_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if not retry(db, job_id, redrive=True):
        raise HTTPException(409, "Only dead-letter jobs can be returned to the queue")
    return _job(db.get(BackgroundJob, job_id))
