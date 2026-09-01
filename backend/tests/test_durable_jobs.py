from datetime import timedelta

from app.jobs.queue import cancel, claim, enqueue, fail, metrics, recover_expired, retry, safe_error, succeed, utcnow
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
    completed = db_session.get(BackgroundJob, job.id)
    assert completed.status == "completed"
    assert completed.progress == 100
    assert completed.duration_ms is not None


def test_failed_job_retries_then_enters_dead_letter(db_session):
    job = enqueue(db_session, "example", {}, max_attempts=2)
    claimed = claim(db_session, "worker-1")
    assert fail(db_session, claimed.id, "worker-1", "temporary") == "retrying"
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
    assert db_session.get(BackgroundJob, job.id).status == "retrying"


def test_operator_cancel_retry_and_redrive(db_session):
    queued = enqueue(db_session, "example", {})
    assert cancel(db_session, queued.id)
    assert claim(db_session, "worker") is None

    failed = enqueue(db_session, "example", {})
    claimed = claim(db_session, "worker")
    assert fail(db_session, claimed.id, "worker", ValueError("invalid"), retryable=False) == "failed"
    assert retry(db_session, failed.id)

    dead = enqueue(db_session, "example", {}, max_attempts=1)
    # The retried failed job is older and is claimed first.
    claimed = claim(db_session, "worker")
    assert succeed(db_session, claimed.id, "worker")
    claimed = claim(db_session, "worker")
    assert claimed.id == dead.id
    assert fail(db_session, dead.id, "worker", RuntimeError("boom")) == "dead_letter"
    assert retry(db_session, dead.id, redrive=True)
    assert db_session.get(BackgroundJob, dead.id).attempts == 0


def test_metrics_and_error_redaction(db_session):
    enqueue(db_session, "example", {})
    snapshot = metrics(db_session)
    assert snapshot["queue_length"] == 1
    assert snapshot["oldest_job_age_seconds"] >= 0
    redacted = safe_error(ValueError("token=abc https://private.example/document"))
    assert "abc" not in redacted
    assert "private.example" not in redacted
