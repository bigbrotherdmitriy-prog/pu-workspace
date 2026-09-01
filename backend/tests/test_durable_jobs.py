from datetime import timedelta

from app.jobs.queue import claim, enqueue, fail, recover_expired, succeed, utcnow
from app.models.job import BackgroundJob


def test_job_is_durable_idempotent_and_claimed(db_session):
    first = enqueue(db_session, "example", {"value": 1}, idempotency_key="same")
    second = enqueue(db_session, "example", {"value": 2}, idempotency_key="same")
    assert second.id == first.id
    job = claim(db_session, "worker-1", lease_seconds=60)
    assert job.id == first.id
    assert job.status == "running"
    assert job.attempts == 1
    assert succeed(db_session, job.id, "worker-1", {"ok": True})
    assert db_session.get(BackgroundJob, job.id).status == "succeeded"


def test_failed_job_retries_then_enters_dead_letter(db_session):
    job = enqueue(db_session, "example", {}, max_attempts=2)
    claimed = claim(db_session, "worker-1")
    assert fail(db_session, claimed.id, "worker-1", "temporary") == "queued"
    claimed = db_session.get(BackgroundJob, job.id)
    claimed.available_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    claimed = claim(db_session, "worker-2")
    assert fail(db_session, claimed.id, "worker-2", "permanent") == "dead_letter"


def test_expired_lease_is_recovered(db_session):
    job = enqueue(db_session, "example", {})
    claimed = claim(db_session, "worker-1")
    claimed.lease_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert recover_expired(db_session) == 1
    assert db_session.get(BackgroundJob, job.id).status == "queued"
