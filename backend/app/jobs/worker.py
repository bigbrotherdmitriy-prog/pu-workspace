from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from threading import Event, Thread

from app.database import SessionLocal
from app.jobs.handlers import run
from app.jobs.queue import claim, fail, heartbeat, recover_expired, succeed, touch_service

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("pu.jobs.worker")


def main() -> None:
    worker_id = os.getenv("PU_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    poll_seconds = max(0.2, float(os.getenv("PU_JOB_POLL_SECONDS", "1")))
    lease_seconds = max(60, int(os.getenv("PU_JOB_LEASE_SECONDS", "900")))
    log.info("Worker %s started", worker_id)
    while True:
        with SessionLocal() as db:
            touch_service(db, worker_id, "worker", {"lease_seconds": lease_seconds})
            recover_expired(db)
            job = claim(db, worker_id, lease_seconds)
        if job is None:
            time.sleep(poll_seconds)
            continue
        heartbeat_stop = Event()

        def keep_lease() -> None:
            while not heartbeat_stop.wait(max(20, lease_seconds // 3)):
                with SessionLocal() as heartbeat_db:
                    if not heartbeat(heartbeat_db, job.id, worker_id, lease_seconds):
                        return

        heartbeat_thread = Thread(target=keep_lease, name=f"job-heartbeat-{job.id}", daemon=True)
        heartbeat_thread.start()
        try:
            result = run(job.kind, dict(job.payload or {}))
        except Exception as exc:
            log.exception("Job %s (%s) failed", job.id, job.kind)
            with SessionLocal() as db:
                fail(db, job.id, worker_id, f"{exc.__class__.__name__}: {exc}")
        else:
            with SessionLocal() as db:
                if not succeed(db, job.id, worker_id, result):
                    log.error("Lease ownership was lost for completed job %s", job.id)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)


if __name__ == "__main__":
    main()
