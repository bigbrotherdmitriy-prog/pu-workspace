"""Telegram reminders before/after a mailbox authority grant lapses."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import mailbox_authority_alerts as alerts
from app.models.audit_log import AuditLog
from test_mailbox_authority_renewal import renew, world  # noqa: F401  (fixture)


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(alerts, "telegram_configured", lambda: True)
    monkeypatch.setattr(alerts, "notify_telegram", lambda text: messages.append(text) or True)
    return messages


def run(w, now):
    sessions = sessionmaker(bind=w.db.get_bind(), expire_on_commit=False)
    w.db.commit()
    count = alerts.notify_mailbox_authority_expiry_once(sessions=sessions, now=now)
    w.db.expire_all()
    return count


def markers(w):
    return list(w.db.scalars(select(AuditLog.details).where(
        AuditLog.action == alerts.ALERT_AUDIT_ACTION).order_by(AuditLog.id)))


def test_far_expiry_is_silent(world, sent):
    world.authority.valid_until = world.now + timedelta(days=3)
    assert run(world, world.now) == 0 and sent == []


def test_expiring_then_expired_each_alert_once(world, sent):
    w = world
    w.authority.valid_until = w.now + timedelta(hours=5)
    assert run(w, w.now) == 1
    assert run(w, w.now + timedelta(hours=1)) == 0          # same window: no repeat
    assert "истекает" in sent[0] and "Продлить доступ к почте на 7 дней" in sent[0]
    assert run(w, w.now + timedelta(hours=6)) == 1           # now lapsed
    assert run(w, w.now + timedelta(hours=7)) == 0
    assert "истёк" in sent[1] and "не загружаются" in sent[1]
    assert [m.split(";")[0] for m in markers(w)] == ["event=expiring", "event=expired"]
    # No mailbox address or other content leaks into the alert.
    assert all("@" not in text for text in sent)


def test_renewal_rearms_the_reminder(world, sent):
    w = world
    w.authority.valid_until = w.now + timedelta(hours=5)
    assert run(w, w.now) == 1
    w.authority.valid_until = w.now + timedelta(days=7) - timedelta(hours=2)
    assert run(w, w.now + timedelta(days=6)) == 1             # new window, new alert
    assert len(sent) == 2


def test_failed_delivery_leaves_no_marker_and_retries(world, monkeypatch):
    w = world
    w.authority.valid_until = w.now + timedelta(hours=5)
    monkeypatch.setattr(alerts, "telegram_configured", lambda: True)
    monkeypatch.setattr(alerts, "notify_telegram", lambda text: False)
    assert run(w, w.now) == 0 and markers(w) == []
    monkeypatch.setattr(alerts, "notify_telegram", lambda text: True)
    assert run(w, w.now) == 1


def test_revoked_grant_and_unconfigured_telegram_are_silent(world, sent, monkeypatch):
    w = world
    w.authority.valid_until = w.now + timedelta(hours=5)
    w.authority.state = "revoked"
    assert run(w, w.now) == 0
    w.authority.state = "active"
    monkeypatch.setattr(alerts, "telegram_configured", lambda: False)
    assert run(w, w.now) == 0 and sent == []


def test_long_lapsed_grant_is_not_scanned_again(world, sent):
    w = world
    w.authority.valid_until = w.now - timedelta(days=3)
    assert run(w, w.now) == 0 and sent == []


def test_exception_in_telegram_keeps_nothing_half_written(world, monkeypatch):
    w = world
    w.authority.valid_until = w.now + timedelta(hours=5)
    monkeypatch.setattr(alerts, "telegram_configured", lambda: True)

    def boom(text):
        raise RuntimeError("network")
    monkeypatch.setattr(alerts, "notify_telegram", boom)
    with pytest.raises(RuntimeError):
        run(w, w.now)
    assert markers(w) == []
