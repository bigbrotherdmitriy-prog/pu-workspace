from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch

from app.automations import gmail
from app.models.google_token import GoogleOAuthToken
from app.models.organization_contract import Organization
from app.models.project import Project


def test_gmail_automation_is_opt_in_outside_compose():
    with patch.dict("os.environ", {}, clear=True):
        assert gmail.enabled() is False
        assert gmail.interval_seconds() == 300


def test_gmail_automation_interval_has_safe_minimum():
    with patch.dict("os.environ", {"GMAIL_AUTO_SYNC_INTERVAL_SECONDS": "5"}, clear=True):
        assert gmail.interval_seconds() == 60


def test_gmail_automation_status_is_observable():
    with patch.dict("os.environ", {"GMAIL_AUTO_SYNC_ENABLED": "true", "GMAIL_AUTO_SYNC_INTERVAL_SECONDS": "600"}, clear=True):
        result = gmail.status()
        assert result["enabled"] is True
        assert result["interval_seconds"] == 600
        assert {"last_run_at", "last_result", "last_error", "lock_scope"} <= result.keys()


def test_gmail_automation_uses_process_lock_outside_postgresql(monkeypatch):
    monkeypatch.setattr(gmail.engine.dialect, "name", "sqlite")
    with gmail._exclusive_run() as first:
        with gmail._exclusive_run() as second:
            assert first is True
            assert second is False


def test_gmail_automation_releases_process_lock_after_error(monkeypatch):
    monkeypatch.setattr(gmail.engine.dialect, "name", "sqlite")
    try:
        with gmail._exclusive_run() as acquired:
            assert acquired is True
            raise RuntimeError("simulated sync failure")
    except RuntimeError:
        pass

    with gmail._exclusive_run() as acquired_again:
        assert acquired_again is True


def test_gmail_automation_never_syncs_archived_projects(db_session, user_factory, monkeypatch):
    """A Gmail OAuth row survives archiving its project; the sync pass must not.

    Regression: an OAuth connection left pointed at a project that was still
    active when connected, but later got archived, kept receiving real client
    mail with nowhere confirmed to route it -- for a week, silently.
    """
    org = Organization(name="Synthetic Organization")
    db_session.add(org); db_session.flush()
    active = Project(name="Active Project", organization_id=org.id)
    archived = Project(name="Archived Project", organization_id=org.id,
                       archived_at=datetime.now(timezone.utc))
    db_session.add_all([active, archived]); db_session.flush()
    db_session.add_all([
        GoogleOAuthToken(project_id=active.id, access_token="a", refresh_token="a"),
        GoogleOAuthToken(project_id=archived.id, access_token="b", refresh_token="b"),
    ])
    user_factory(is_admin=True)
    db_session.commit()

    monkeypatch.setattr(gmail, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(gmail.engine.dialect, "name", "sqlite")
    synced_project_ids = []

    def fake_sync(project_id, db, user, *, query, max_results):
        synced_project_ids.append(project_id)
        return {"processed": 0, "skipped": 0, "failed": 0}

    with patch("app.api.gmail.sync_gmail_project", fake_sync):
        totals = gmail.sync_authorized_projects_once()

    assert synced_project_ids == [active.id]
    assert totals["projects"] == 1
