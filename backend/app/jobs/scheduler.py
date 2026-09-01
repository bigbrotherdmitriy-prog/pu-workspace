from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from app.automations.ai_secretary import enabled as ai_enabled, interval_seconds as ai_interval
from app.automations.gmail import enabled as gmail_enabled, interval_seconds as gmail_interval
from app.database import SessionLocal
from app.jobs.queue import enqueue, touch_service

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("pu.jobs.scheduler")


def _bucket(kind: str, interval: int, now: datetime) -> str:
    return f"schedule:{kind}:{int(now.timestamp()) // interval}"


def schedule_once(now: datetime | None = None, service_id: str = "scheduler") -> int:
    now = now or datetime.now(timezone.utc)
    created = 0
    with SessionLocal() as db:
        touch_service(db, service_id, "scheduler")
        for kind, is_enabled, interval in (
            ("gmail.sync", gmail_enabled(), gmail_interval()),
            ("ai.rules", ai_enabled(), ai_interval()),
        ):
            if not is_enabled:
                continue
            job = enqueue(db, kind, {}, idempotency_key=_bucket(kind, interval, now))
            created += int(job.status == "queued" and job.attempts == 0)
    return created


def main() -> None:
    log.info("Scheduler started")
    # One-time cutover recovery: convert unfinished legacy in-process work to
    # durable idempotent jobs after a deployment or crash.
    from app.api.workspace import recover_incomplete_analyses, recover_incomplete_snapshots
    from app.organizer import recover_incomplete_scans
    recover_incomplete_scans()
    recover_incomplete_snapshots()
    recover_incomplete_analyses()
    while True:
        try:
            schedule_once()
        except Exception:
            log.exception("Scheduler pass failed")
        time.sleep(max(5, int(os.getenv("PU_SCHEDULER_TICK_SECONDS", "15"))))


if __name__ == "__main__":
    main()
