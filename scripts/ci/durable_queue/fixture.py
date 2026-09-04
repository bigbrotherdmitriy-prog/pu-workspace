"""Synthetic-only API/handler. Not imported or shipped by the production image.

Uses the real queue, worker main, auth, admin API and audit table. No external I/O.
"""
import sys
import time
from datetime import timedelta

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func

from app.main import app
from app.core.auth import require_admin
from app.database import SessionLocal, get_db
from app.jobs import queue
from app.models.job import BackgroundJob
from app.models.audit_log import AuditLog


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hold: int = Field(default=0, ge=0, le=120)
    failures: int = Field(default=0, ge=0, le=5)
    permanent: bool = False
    max_attempts: int = Field(default=3, ge=1, le=5)
    delay: int = Field(default=0, ge=0, le=600)


@app.post("/ci/jobs", dependencies=[Depends(require_admin)])
def create(payload: Probe, idempotency_key: str = Header(min_length=1, max_length=100), db=Depends(get_db)):
    if not idempotency_key.startswith("ci-"):
        raise HTTPException(422)
    job = queue.enqueue(db, "ci.probe", {"hold": payload.hold, "failures": payload.failures, "permanent": payload.permanent},
                        max_attempts=payload.max_attempts,
                        available_at=queue.utcnow() + timedelta(seconds=payload.delay),
                        idempotency_key=idempotency_key)
    return {"id": job.id}


@app.get("/ci/jobs/{job_id}", dependencies=[Depends(require_admin)])
def state(job_id: int, db=Depends(get_db)):
    row = db.get(BackgroundJob, job_id)
    if row is None:
        raise HTTPException(404)
    return {"id": row.id, "status": row.status, "attempts": row.attempts,
            "worker": row.worker_id, "progress": row.progress, "error": row.last_error,
            "lease": row.lease_expires_at, "available_at": row.available_at,
            "effects": db.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "ci_effect", AuditLog.entity_id == row.id))}


def probe(kind, payload):
    if kind != "ci.probe":
        raise ValueError("Only synthetic jobs are allowed")
    job_id, owner = queue._execution_owner.get()
    with SessionLocal() as db:
        # Fence and deduplicate this synthetic DB side effect in one transaction.
        row = db.scalar(select(BackgroundJob).where(*queue._live_owner(job_id, owner)).with_for_update())
        if row is None:
            raise RuntimeError("Lost lease")
        attempt = row.attempts
        exists = db.scalar(select(AuditLog.id).where(AuditLog.action == "ci_effect", AuditLog.entity_id == job_id))
        if exists is None:
            db.add(AuditLog(action="ci_effect", entity_type="ci_job", entity_id=job_id, details="synthetic"))
        db.commit()
        queue.set_progress(db, job_id, owner, 25)
    # Only first delivery is held, so SIGKILL recovery completes promptly.
    if attempt == 1:
        for _ in range(payload["hold"]):
            time.sleep(1)
            with SessionLocal() as db:
                if not queue.set_progress(db, job_id, owner, 40):
                    raise RuntimeError("Lost lease")
    if attempt <= payload["failures"]:
        if payload.get("permanent"):
            raise ValueError("CI_DOCUMENT_SENTINEL CI_SECRET_SENTINEL")
        raise TimeoutError("CI_DOCUMENT_SENTINEL CI_SECRET_SENTINEL")
    return {"synthetic": True}


if __name__ == "__main__":
    assert sys.argv[1:] == ["worker"]
    from app.jobs import worker
    worker.run = probe
    worker.main()
