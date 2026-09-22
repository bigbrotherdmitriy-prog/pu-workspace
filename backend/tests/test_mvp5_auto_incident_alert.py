from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.pilot_dispatch import PRODUCT_KIND
from app.pilot_incidents import ALERT_AUDIT_ACTION, notify_product_auto_incidents_once


def _sessions(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'incident-alert.db'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def _job(*, kind=PRODUCT_KIND, status="dead_letter", marker="safe"):
    return BackgroundJob(
        kind=kind,
        payload={"marker": marker},
        status=status,
        attempts=3,
        max_attempts=3,
        priority=100,
        progress=0,
    )


def test_product_auto_terminal_incident_alert_is_content_free_and_deduplicated(
    tmp_path, monkeypatch,
):
    engine, sessions = _sessions(tmp_path)
    messages = []
    monkeypatch.setattr("app.pilot_incidents.telegram_configured", lambda: True)
    monkeypatch.setattr(
        "app.pilot_incidents.notify_telegram",
        lambda message: messages.append(message) or True,
    )
    with sessions.begin() as db:
        product = _job(marker="must-not-leak")
        db.add(product)
        db.add(_job(kind="provider.action.reconcile", marker="not-this-job"))
        db.flush()
        product_id = product.id

    assert notify_product_auto_incidents_once(sessions=sessions) == 1
    assert notify_product_auto_incidents_once(sessions=sessions) == 0
    assert len(messages) == 1
    assert f"job_id={product_id}" in messages[0]
    assert "status=dead_letter" in messages[0]
    assert "must-not-leak" not in messages[0]
    assert "not-this-job" not in messages[0]
    with sessions() as db:
        audit = db.scalar(select(AuditLog).where(
            AuditLog.action == ALERT_AUDIT_ACTION,
            AuditLog.entity_id == product_id,
        ))
        assert audit is not None
        assert audit.details == f"kind={PRODUCT_KIND};status=dead_letter;channel=telegram"
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == ALERT_AUDIT_ACTION,
        )) == 1
    engine.dispose()


def test_product_auto_failed_job_is_alerted_but_nonterminal_is_not(tmp_path, monkeypatch):
    engine, sessions = _sessions(tmp_path)
    messages = []
    monkeypatch.setattr("app.pilot_incidents.telegram_configured", lambda: True)
    monkeypatch.setattr(
        "app.pilot_incidents.notify_telegram",
        lambda message: messages.append(message) or True,
    )
    with sessions.begin() as db:
        failed = _job(status="failed")
        retrying = _job(status="retrying")
        db.add_all([failed, retrying])
        db.flush()
        failed_id = failed.id

    assert notify_product_auto_incidents_once(sessions=sessions) == 1
    assert len(messages) == 1 and f"job_id={failed_id}" in messages[0]
    assert "status=failed" in messages[0]
    engine.dispose()


def test_telegram_failure_does_not_mark_incident_and_is_retried(tmp_path, monkeypatch):
    engine, sessions = _sessions(tmp_path)
    outcomes = iter((False, True))
    monkeypatch.setattr("app.pilot_incidents.telegram_configured", lambda: True)
    monkeypatch.setattr("app.pilot_incidents.notify_telegram", lambda _message: next(outcomes))
    with sessions.begin() as db:
        db.add(_job())

    assert notify_product_auto_incidents_once(sessions=sessions) == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == ALERT_AUDIT_ACTION,
        )) == 0
    assert notify_product_auto_incidents_once(sessions=sessions) == 1
    engine.dispose()


def test_unconfigured_telegram_is_a_noop(tmp_path, monkeypatch):
    engine, sessions = _sessions(tmp_path)
    monkeypatch.setattr("app.pilot_incidents.telegram_configured", lambda: False)
    with sessions.begin() as db:
        db.add(_job())

    assert notify_product_auto_incidents_once(sessions=sessions) == 0
    engine.dispose()
