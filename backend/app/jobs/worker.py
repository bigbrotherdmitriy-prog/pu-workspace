from __future__ import annotations

import logging
import os
import socket
import signal
import time
import uuid
from threading import Event, Thread

from app.database import SessionLocal
from app.jobs.handlers import notify_outcome, run
from app.jobs.queue import claim, execution_owner, fail, heartbeat, recover_expired, set_progress, succeed, touch_service

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("pu.jobs.worker")


def main() -> None:
    from app.staging.ci_local_upload import install_ci_local_upload_runtime
    install_ci_local_upload_runtime()
    # A configured label must not let a restarted process inherit an old lease.
    worker_id = f"{os.getenv('PU_WORKER_ID') or socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    poll_seconds = max(0.2, float(os.getenv("PU_JOB_POLL_SECONDS", "1")))
    lease_seconds = max(60, int(os.getenv("PU_JOB_LEASE_SECONDS", "900")))
    shutdown = Event()
    def stop(signum, _frame):
        log.info("Worker %s received signal %s; draining current job", worker_id, signum)
        shutdown.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("Worker %s started", worker_id)
    while not shutdown.is_set():
        with SessionLocal() as db:
            touch_service(db, worker_id, "worker", {"lease_seconds": lease_seconds})
            recover_expired(db)
            job = claim(db, worker_id, lease_seconds)
        if job is None:
            shutdown.wait(poll_seconds)
            continue
        heartbeat_stop = Event()

        def keep_lease(job=job, heartbeat_stop=heartbeat_stop) -> None:
            while not heartbeat_stop.wait(max(20, lease_seconds // 3)):
                with SessionLocal() as heartbeat_db:
                    if not heartbeat(heartbeat_db, job.id, worker_id, lease_seconds):
                        return
                    touch_service(heartbeat_db, worker_id, "worker", {"lease_seconds": lease_seconds, "job_id": job.id})

        heartbeat_thread = Thread(target=keep_lease, name=f"job-heartbeat-{job.id}", daemon=True)
        heartbeat_thread.start()
        try:
            with SessionLocal() as db:
                set_progress(db, job.id, worker_id, 10)
            with execution_owner(job.id, worker_id, attempt=job.attempts, locked_at=job.locked_at):
                result = run(job.kind, dict(job.payload or {}))
        except Exception as exc:
            log.error("Job %s (%s) failed; error_type=%s", job.id, job.kind, exc.__class__.__name__)
            with SessionLocal() as db:
                status = fail(db, job.id, worker_id, exc, retryable=not isinstance(exc, (KeyError, TypeError, ValueError)))
            if status != "lost":
                try:
                    notify_outcome(job.kind, dict(job.payload or {}), status)
                except Exception as hook_error:
                    log.error("Job %s lifecycle hook failed; error_type=%s", job.id, hook_error.__class__.__name__)
        else:
            with SessionLocal() as db:
                set_progress(db, job.id, worker_id, 95)
                completed = succeed(db, job.id, worker_id, result)
                if not completed:
                    log.error("Lease ownership was lost for completed job %s", job.id)
            if completed:
                try:
                    notify_outcome(job.kind, dict(job.payload or {}),
                                   "cancelled" if (result or {}).get("cancelled") else "completed")
                except Exception as hook_error:
                    log.error("Job %s lifecycle hook failed; error_type=%s", job.id, hook_error.__class__.__name__)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
    log.info("Worker %s stopped", worker_id)


if __name__ == "__main__":
    main()
