from datetime import datetime, timezone

from app import executive_docs
from app.main import app


def _enqueue_calls(monkeypatch):
    calls = []

    def fake_enqueue(db, kind, payload, idempotency_key=None, **kwargs):
        calls.append({"kind": kind, "payload": payload, "idempotency_key": idempotency_key})

        class _Job:
            status = "queued"
            attempts = 0

        return _Job()

    monkeypatch.setattr(executive_docs, "enqueue", fake_enqueue)
    return calls


def test_schedule_daily_scan_before_hour(monkeypatch):
    monkeypatch.delenv("PU_ID_SCAN_ENABLED", raising=False)
    monkeypatch.delenv("PU_ID_SCAN_HOUR", raising=False)
    monkeypatch.delenv("PU_ID_SCAN_TZ", raising=False)
    calls = _enqueue_calls(monkeypatch)
    # 05:59 Moscow == 02:59 UTC
    now = datetime(2025, 1, 15, 2, 59, tzinfo=timezone.utc)
    executive_docs.schedule_daily_scan(None, now=now)
    assert calls == []


def test_schedule_daily_scan_at_hour(monkeypatch):
    monkeypatch.delenv("PU_ID_SCAN_ENABLED", raising=False)
    monkeypatch.delenv("PU_ID_SCAN_HOUR", raising=False)
    monkeypatch.delenv("PU_ID_SCAN_TZ", raising=False)
    calls = _enqueue_calls(monkeypatch)
    # 06:00 Moscow == 03:00 UTC
    now = datetime(2025, 1, 15, 3, 0, tzinfo=timezone.utc)
    executive_docs.schedule_daily_scan(None, now=now)
    assert len(calls) == 1
    assert calls[0]["kind"] == "executive_docs.scan"
    assert calls[0]["idempotency_key"] == "executive_docs.scan:2025-01-15"


def test_schedule_daily_scan_disabled(monkeypatch):
    monkeypatch.setenv("PU_ID_SCAN_ENABLED", "false")
    calls = _enqueue_calls(monkeypatch)
    now = datetime(2025, 1, 15, 3, 0, tzinfo=timezone.utc)
    executive_docs.schedule_daily_scan(None, now=now)
    assert calls == []


def test_executive_docs_router_registered():
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/projects/{project_id}/executive-docs/report" in paths
