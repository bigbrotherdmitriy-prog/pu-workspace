"""AUTO pilot may run a 7-day window (owner decision 13.10.2026, TZ V6-07)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import pilot_expiry_alerts as alerts
from app.core.v54_authority import BOOTSTRAP_VALID_FOR
from app.models.audit_log import AuditLog
from app.models.project import Project
from app.pilot_product import MAX_POLICY_TTL_HOURS, ProductPilotSettings
from app.action_trust.guards import TrustConflict

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


def settings(ttl):
    return ProductPilotSettings(enabled=True, project_id=17, owner_user_id=1, hourly_quota=3,
                                policy_ttl_hours=ttl, minimum_confidence=0.9, notification_enabled=True)


@pytest.mark.parametrize("ttl", [24, 48, 72, 168])
def test_policy_may_last_up_to_seven_days(ttl):
    assert settings(ttl).policy_ttl_hours == ttl


@pytest.mark.parametrize("ttl", [23, MAX_POLICY_TTL_HOURS + 1])
def test_policy_ttl_outside_bounds_is_rejected(ttl):
    with pytest.raises(TrustConflict):
        settings(ttl)


def test_mandate_covers_the_observation_window():
    assert BOOTSTRAP_VALID_FOR == timedelta(days=7)


@pytest.fixture
def world(db_session, monkeypatch):
    sent = []
    db_session.add(Project(id=17, name="Дубна и Городец", organization_id=1)); db_session.commit()
    state = {"policy": {"task_mode": "AUTO", "notification_mode": "AUTO", "valid_until": NOW + timedelta(days=3)},
             "authority": {"valid_until": NOW + timedelta(days=3)}}
    monkeypatch.setattr(alerts, "telegram_configured", lambda: True)
    monkeypatch.setattr(alerts, "notify_telegram", lambda text: sent.append(text) or True)
    monkeypatch.setattr("app.pilot_product.load_product_pilot_settings",
                        lambda: SimpleNamespace(enabled=True, project_id=17, owner_user_id=1))
    monkeypatch.setattr("app.autonomy_readiness.project_autonomy_readiness", lambda *a, **k: state)
    return db_session, state, sent


def run(db, now=NOW):
    count = alerts.notify_pilot_expiry_once(sessions=sessionmaker(bind=db.get_bind(), expire_on_commit=False), now=now)
    db.expire_all()
    return count


def test_far_expiry_is_silent(world):
    db, _state, sent = world
    assert run(db) == 0 and sent == []


def test_policy_and_mandate_each_alert_once_per_window(world):
    db, state, sent = world
    state["policy"]["valid_until"] = NOW + timedelta(hours=5)
    state["authority"]["valid_until"] = NOW - timedelta(hours=1)
    assert run(db) == 2
    assert run(db, NOW + timedelta(minutes=10)) == 0
    assert any("политика AUTO-пилота истекает" in t for t in sent)
    assert any("мандат AUTO-пилота истёк" in t for t in sent)
    state["policy"]["valid_until"] = NOW + timedelta(hours=6)     # renewed: a new window re-arms
    assert run(db) == 1


def test_confirm_only_policy_never_alerts(world):
    db, state, sent = world
    state["policy"].update(task_mode="CONFIRM", notification_mode="CONFIRM", valid_until=NOW + timedelta(hours=1))
    assert run(db) == 0 and sent == []


def test_long_lapsed_items_are_not_rescanned(world):
    db, state, sent = world
    state["policy"]["valid_until"] = NOW - timedelta(days=3)
    state["authority"]["valid_until"] = NOW - timedelta(days=3)
    assert run(db) == 0
    assert list(db.scalars(select(AuditLog.id).where(AuditLog.action == alerts.ALERT_AUDIT_ACTION))) == []
