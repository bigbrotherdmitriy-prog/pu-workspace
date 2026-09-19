from contextlib import nullcontext
from datetime import date, datetime, timezone
from unittest.mock import patch

from sqlalchemy import select

from app.api import management as management_api
from app.automations import notifications
from app.jobs import scheduler
from app.jobs.handlers import run
from app.models.job import BackgroundJob
from app.models.management import Notification, NotificationPolicy, Obligation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _deadline_world(db, user_factory, *, archived=False):
    organization = Organization(name="Deadline automation tenant")
    user = user_factory()
    db.add(organization); db.flush()
    project = Project(name="Deadline automation project", organization_id=organization.id,
                      archived_at=datetime.now(timezone.utc) if archived else None)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="viewer"))
    obligation = Obligation(
        project_id=project.id, owner_user_id=user.id, title="Automatic deadline",
        status="confirmed", due_date=date(2026, 9, 22), source_type="manual",
        source_id="automatic-deadline", source_name="source.txt", source_excerpt="evidence",
        source_hash="a" * 64, confidence=1.0,
    )
    db.add(obligation); db.commit()
    return user, project, obligation


def test_deadline_job_creates_default_policy_and_notification_without_ui(db_session, user_factory, monkeypatch):
    user, project, obligation = _deadline_world(db_session, user_factory)
    monkeypatch.setattr(notifications, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(management_api, "_utcnow",
                        lambda: datetime(2026, 9, 19, 12, tzinfo=timezone.utc))

    assert db_session.scalar(select(NotificationPolicy)) is None
    result = run("notifications.refresh", {})

    assert result == {"projects": 1, "members": 1, "failed": 0}
    policy = db_session.scalar(select(NotificationPolicy))
    notice = db_session.scalar(select(Notification))
    assert (policy.project_id, policy.user_id) == (project.id, user.id)
    assert (notice.entity_type, notice.entity_id, notice.kind) == ("obligation", obligation.id, "deadline")


def test_deadline_job_skips_archived_projects(db_session, user_factory, monkeypatch):
    _deadline_world(db_session, user_factory, archived=True)
    monkeypatch.setattr(notifications, "SessionLocal", lambda: nullcontext(db_session))

    assert run("notifications.refresh", {}) == {"projects": 0, "members": 0, "failed": 0}
    assert db_session.scalar(select(Notification)) is None
    assert db_session.scalar(select(NotificationPolicy)) is None


def test_notification_refresh_configuration_has_safe_defaults_and_minimum():
    with patch.dict("os.environ", {}, clear=True):
        assert notifications.enabled() is True
        assert notifications.interval_seconds() == 300
    with patch.dict("os.environ", {"NOTIFICATION_REFRESH_ENABLED": "false",
                                    "NOTIFICATION_REFRESH_INTERVAL_SECONDS": "5"}, clear=True):
        assert notifications.enabled() is False
        assert notifications.interval_seconds() == 60


def test_scheduler_enqueues_durable_notification_refresh(db_session, monkeypatch):
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler, "gmail_enabled", lambda: False)
    monkeypatch.setattr(scheduler, "ai_enabled", lambda: False)
    monkeypatch.setattr(scheduler, "notifications_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "notifications_interval", lambda: 300)
    monkeypatch.setattr("app.pilot_dispatch.recover_installed", lambda: 0)
    monkeypatch.setattr("app.staging.gmail.recover_gmail_attachment_jobs", lambda: 0)
    monkeypatch.setattr("app.local_upload_staging.recover_local_upload_retention", lambda: 0)

    created = scheduler.schedule_once(now, "test-scheduler")

    job = db_session.scalar(select(BackgroundJob).where(BackgroundJob.kind == "notifications.refresh"))
    assert created == 1
    assert job is not None
    assert job.payload == {}
    assert job.idempotency_key == scheduler._bucket("notifications.refresh", 300, now)
