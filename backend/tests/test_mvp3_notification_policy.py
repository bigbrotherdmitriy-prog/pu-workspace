from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api import management as management_api
from app.api.management import NotificationPolicyUpdate, refresh_notifications, update_notification_policy
from app.jobs.handlers import run
from app.jobs.queue import claim, fail, succeed
from app.models.job import BackgroundJob
from app.models.management import Notification, Obligation
from app.notification_escalation import deadline_utc, outside_quiet_hours
from test_mvp3_hardening import world


def test_dst_gap_moves_forward_and_ambiguous_deadline_uses_later_instant():
    spring = deadline_utc(date(2026, 3, 29), time(2, 30), "Europe/Berlin")
    autumn = deadline_utc(date(2026, 10, 25), time(2, 30), "Europe/Berlin")
    assert spring == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
    assert autumn == datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)


def test_overnight_quiet_window_defers_until_local_end():
    # 20:30 UTC is 23:30 in Moscow and therefore inside 22:00-07:00 quiet hours.
    result = outside_quiet_hours(
        datetime(2026, 9, 4, 20, 30, tzinfo=timezone.utc),
        timezone_name="Europe/Moscow", quiet_start=time(22), quiet_end=time(7),
    )
    assert result == datetime(2026, 9, 5, 4, 0, tzinfo=timezone.utc)


def test_overdue_escalation_respects_current_quiet_window(db_session, user_factory, monkeypatch):
    _, user, project, _ = _due_world(db_session, user_factory)
    # 20:30 UTC is 23:30 in Moscow. An already-due escalation must wait for 07:00 local.
    monkeypatch.setattr(management_api, "_utcnow",
                        lambda: datetime(2026, 9, 4, 20, 30, tzinfo=timezone.utc))
    update_notification_policy(
        project.id,
        NotificationPolicyUpdate(timezone="Europe/Moscow", deadline_local_time=time(9),
                                 quiet_start=time(22), quiet_end=time(7),
                                 escalation_delays=[0], channels=["in_app"]),
        db_session, user,
    )
    refresh_notifications(project.id, db_session, user)
    job = db_session.scalar(select(BackgroundJob))
    assert job.available_at.replace(tzinfo=timezone.utc) == datetime(2026, 9, 5, 4, tzinfo=timezone.utc)


def test_policy_rejects_unknown_timezone_and_unsorted_delays():
    with pytest.raises(ValidationError):
        NotificationPolicyUpdate(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        NotificationPolicyUpdate(escalation_delays=[60, 0])


def _due_world(db, user_factory):
    organization, user, project, _ = world(db, user_factory)
    obligation = Obligation(project_id=project.id, owner_user_id=user.id, title="Deadline",
                            status="confirmed", due_date=date(2026, 9, 4), source_type="manual",
                            source_id="deadline-source", source_name="source.txt", source_excerpt="evidence",
                            source_hash="d" * 64, confidence=1.0)
    db.add(obligation); db.commit()
    return organization, user, project, obligation


def test_refresh_preserves_read_history_and_deduplicates_escalation_jobs(db_session, user_factory, monkeypatch):
    _, user, project, obligation = _due_world(db_session, user_factory)
    monkeypatch.setattr(management_api, "_utcnow",
                        lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc))
    policy = update_notification_policy(
        project.id,
        NotificationPolicyUpdate(timezone="Europe/Moscow", deadline_local_time=time(22, 30),
                                 quiet_start=time(22), quiet_end=time(7),
                                 escalation_delays=[0, 60], channels=["in_app"]),
        db_session, user,
    )
    assert policy["record_version"] == 1
    first = refresh_notifications(project.id, db_session, user)
    notification = db_session.scalar(select(Notification).where(Notification.entity_id == obligation.id))
    assert first["unread"] == 1
    notification.is_read = True; notification.record_version += 1; db_session.commit()

    refresh_notifications(project.id, db_session, user)
    refresh_notifications(project.id, db_session, user)
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 1
    assert db_session.get(Notification, notification.id).is_read is True
    jobs = list(db_session.scalars(select(BackgroundJob).where(
        BackgroundJob.kind == "notifications.escalation.proposal",
    ).order_by(BackgroundJob.id)))
    assert len(jobs) == 2
    assert len({job.idempotency_key for job in jobs}) == 2
    assert all(job.available_at.replace(tzinfo=timezone.utc) >= datetime(2026, 9, 5, 4, tzinfo=timezone.utc)
               for job in jobs)


def test_escalation_proposal_retry_is_safe_and_never_performs_external_action(db_session, user_factory, monkeypatch):
    _, user, project, _ = _due_world(db_session, user_factory)
    now = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(management_api, "_utcnow", lambda: now)
    update_notification_policy(
        project.id,
        NotificationPolicyUpdate(timezone="UTC", deadline_local_time=time(9), quiet_start=time(22),
                                 quiet_end=time(7), escalation_delays=[0], channels=["in_app"]),
        db_session, user,
    )
    refresh_notifications(project.id, db_session, user)
    refresh_notifications(project.id, db_session, user)
    assert db_session.scalar(select(func.count()).select_from(BackgroundJob)) == 1
    job = db_session.scalar(select(BackgroundJob))
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1); db_session.commit()

    claimed = claim(db_session, "mvp3-worker", lease_seconds=60)
    assert claimed.id == job.id
    assert fail(db_session, job.id, "mvp3-worker", RuntimeError("synthetic"), retryable=True) == "retrying"
    retriable = db_session.get(BackgroundJob, job.id)
    retriable.available_at = datetime.now(timezone.utc) - timedelta(seconds=1); db_session.commit()
    claimed = claim(db_session, "mvp3-worker", lease_seconds=60)
    result = run(claimed.kind, dict(claimed.payload))
    assert result["status"] == "proposed"
    assert result["external_action_performed"] is False
    assert succeed(db_session, job.id, "mvp3-worker", result) is True
    assert db_session.get(BackgroundJob, job.id).status == "completed"
