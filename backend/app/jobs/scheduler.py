from __future__ import annotations

import logging
import os
import time
import signal
from threading import Event
from datetime import datetime, timezone

from app.automations.ai_secretary import enabled as ai_enabled, interval_seconds as ai_interval
from app.automations.gmail import enabled as gmail_enabled, interval_seconds as gmail_interval
from app.automations.notifications import enabled as notifications_enabled, interval_seconds as notifications_interval
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
            ("notifications.refresh", notifications_enabled(), notifications_interval()),
        ):
            if not is_enabled:
                continue
            job = enqueue(db, kind, {}, idempotency_key=_bucket(kind, interval, now))
            created += int(job.status == "queued" and job.attempts == 0)
        from app.management_digest import schedule_digest_jobs
        created += schedule_digest_jobs(db, now=now)
    from app.pilot_dispatch import recover_installed
    from app.pilot_incidents import notify_product_auto_incidents_once
    from app.local_upload_staging import recover_local_upload_retention
    from app.staging.gmail import recover_gmail_attachment_jobs
    recovered = (
        created + recover_installed() + recover_gmail_attachment_jobs()
        + recover_local_upload_retention()
    )
    # Operational projection only: Telegram failure never changes durable work.
    notify_product_auto_incidents_once()
    return recovered


def main() -> None:
    from app.staging.local_upload_composition import install_local_upload_runtime
    from app.pilot_product import install_product_pilot_runtime
    install_local_upload_runtime()
    install_product_pilot_runtime()
    shutdown = Event()
    def stop(signum, _frame):
        log.info("Scheduler received signal %s", signum)
        shutdown.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("Scheduler started")
    # One-time cutover recovery: convert unfinished legacy in-process work to
    # durable idempotent jobs after a deployment or crash.
    from app.api.workspace import recover_incomplete_analyses, recover_incomplete_snapshots
    from app.organizer import recover_incomplete_scans
    recover_incomplete_scans()
    recover_incomplete_snapshots()
    recover_incomplete_analyses()
    while not shutdown.is_set():
        try:
            schedule_once()
        except Exception:
            log.exception("Scheduler pass failed")
        shutdown.wait(max(5, int(os.getenv("PU_SCHEDULER_TICK_SECONDS", "15"))))
    log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
