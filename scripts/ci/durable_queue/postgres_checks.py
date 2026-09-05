"""Real PostgreSQL process race before the runtime workers start."""
import multiprocessing as mp
from datetime import timedelta

from sqlalchemy import select
from app.database import SessionLocal
from app.jobs.queue import enqueue, claim, recover_expired, heartbeat, set_progress, succeed, fail, utcnow
from app.models.job import BackgroundJob


def racer(start, output, number):
    start.wait(10)
    with SessionLocal() as db:
        row = claim(db, f"race-{number}", 60)
        output.put(None if row is None else (row.id, row.worker_id))


def main():
    with SessionLocal() as db:
        job = enqueue(db, "ci.probe", {"hold": 0, "failures": 0}, idempotency_key="ci-process-race")
        job_id = job.id
    ctx = mp.get_context("spawn")
    start, output = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=racer, args=(start, output, i)) for i in range(2)]
    for p in processes:
        p.start()
    start.set()
    try:
        results = [output.get(timeout=20) for _ in processes]
        winners = [r for r in results if r is not None]
        assert len(winners) == 1 and winners[0][0] == job_id
        with SessionLocal() as db:
            row = db.get(BackgroundJob, job_id)
            assert row.attempts == 1
            old = row.worker_id
            row.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
            assert not heartbeat(db, job_id, old)
            assert not succeed(db, job_id, old)
            assert recover_expired(db) == 1
            new = claim(db, "race-recovery")
            assert new.id == job_id and new.attempts == 2
            assert not set_progress(db, job_id, old, 90)
            assert fail(db, job_id, old, RuntimeError()) == "lost"
            assert succeed(db, job_id, "race-recovery")
        print(f'{{"race_job_id":{job_id},"single_owner":true,"stale_rejected":true}}')
    finally:
        for p in processes:
            p.join(5)
            if p.is_alive():
                p.kill()
            assert p.exitcode == 0


if __name__ == "__main__":
    main()
