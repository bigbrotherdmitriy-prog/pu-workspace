"""Owner is told when a project's mail silently stops loading."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import gmail_sync_health as health
from app.models.audit_log import AuditLog
from app.models.project import Project

NOW = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
OK = "query=is:inbox newer_than:7d; processed=3; skipped=1; failed=0; reclassified=0"
ALL_FAIL = ("query=is:inbox newer_than:7d; processed=0; skipped=0; failed=17; reclassified=0; "
            "errors=ValueError")


@pytest.fixture
def world(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(health, "telegram_configured", lambda: True)
    monkeypatch.setattr(health, "notify_telegram", lambda text: sent.append(text) or True)
    db_session.add_all([Project(id=17, name="Дубна и Городец", organization_id=1),
                        Project(id=24, name="СБП", organization_id=1)])
    db_session.commit()
    return db_session, sent


def record(db, project_id, details, minutes_ago, action="gmail_sync"):
    db.add(AuditLog(action=action, entity_type="project", entity_id=project_id, details=details,
                    created_at=NOW - timedelta(minutes=minutes_ago)))
    db.commit()


def run(db, now=NOW):
    sessions = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    count = health.notify_gmail_sync_health_once(sessions=sessions, now=now)
    db.expire_all()
    return count


def test_three_fully_failing_sweeps_alert_once_per_day(world):
    db, sent = world
    for minutes in (10, 5, 0):
        record(db, 17, ALL_FAIL, minutes)
    assert run(db) == 1
    assert "Дубна и Городец" in sent[0] and "ValueError" in sent[0] and "отклоняются" in sent[0]
    assert run(db, NOW + timedelta(minutes=5)) == 0
    record(db, 17, ALL_FAIL, -1440)
    for minutes in (-1430, -1435):
        record(db, 17, ALL_FAIL, minutes)
    assert run(db, NOW + timedelta(days=1)) == 1


def test_one_good_sweep_in_the_streak_means_healthy(world):
    db, sent = world
    record(db, 17, ALL_FAIL, 10)
    record(db, 17, OK, 5)
    record(db, 17, ALL_FAIL, 0)
    assert run(db) == 0 and sent == []


def test_fewer_than_three_failures_is_not_yet_an_incident(world):
    db, sent = world
    record(db, 17, ALL_FAIL, 5)
    record(db, 17, ALL_FAIL, 0)
    assert run(db) == 0


def test_dead_connection_is_reported_separately(world):
    db, sent = world
    for minutes in (10, 5, 0):
        record(db, 24, "error=RefreshError", minutes, action="gmail_auto_sync_failed")
    assert run(db) == 1
    assert "СБП" in sent[0] and "подключиться" in sent[0]
    markers = list(db.scalars(select(AuditLog.details).where(AuditLog.action == health.ALERT_AUDIT_ACTION)))
    assert markers == ["kind=connection;date=2026-10-13"]


def test_archived_project_and_unconfigured_telegram_are_silent(world, monkeypatch):
    db, sent = world
    db.get(Project, 17).archived_at = NOW
    db.commit()
    for minutes in (10, 5, 0):
        record(db, 17, ALL_FAIL, minutes)
    assert run(db) == 0
    monkeypatch.setattr(health, "telegram_configured", lambda: False)
    assert run(db) == 0 and sent == []
