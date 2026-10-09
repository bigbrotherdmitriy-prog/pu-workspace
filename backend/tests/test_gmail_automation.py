import base64
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app.automations import gmail
from app.mailbox_identity.service import MailboxIdentityService
from app.models.ai_secretary import Message
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import MailboxProjectCohort
from app.models.organization_contract import Organization
from app.models.project import Project

NOW = datetime.now(timezone.utc)


def test_gmail_automation_is_opt_in_outside_compose():
    with patch.dict("os.environ", {}, clear=True):
        assert gmail.enabled() is False
        assert gmail.background_sweep_enabled() is False
        assert gmail.interval_seconds() == 300


def test_all_project_sweep_requires_separate_explicit_opt_in():
    with patch.dict("os.environ", {"GMAIL_AUTO_SYNC_ENABLED": "true"}, clear=True):
        assert gmail.enabled() is True
        assert gmail.background_sweep_enabled() is False
    with patch.dict("os.environ", {
        "GMAIL_AUTO_SYNC_ENABLED": "true",
        "GMAIL_BACKGROUND_SWEEP_ENABLED": "true",
    }, clear=True):
        assert gmail.background_sweep_enabled() is True


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


def _gmail_item(message_id: str, *, subject: str = "Synthetic") -> dict:
    return {
        "id": message_id, "threadId": f"thread-{message_id}", "historyId": f"history-{message_id}",
        "labelIds": ["INBOX"],
        "payload": {"mimeType": "text/plain", "headers": [
            {"name": "Subject", "value": subject},
            {"name": "From", "value": "client@example.test"}],
            "body": {"data": base64.urlsafe_b64encode(b"Synthetic body").decode()}},
    }


def _fake_gmail_service(items: list[dict]):
    class FakeGmail:
        def users(self): return self
        def messages(self): return self
        def list(self, **kwargs): return SimpleNamespace(execute=lambda: {"messages": [{"id": i["id"]} for i in items]})
        def get(self, **kwargs):
            item_id = kwargs["id"]
            return SimpleNamespace(execute=lambda: next(i for i in items if i["id"] == item_id))
    return SimpleNamespace(service=lambda *a: FakeGmail())


def _patch_ai_secretary_engines(monkeypatch):
    from app.api import ai_secretary
    monkeypatch.setattr(ai_secretary, "create_tasks_from_files", lambda *a, **k: [])
    monkeypatch.setattr(ai_secretary, "create_response_drafts", lambda *a, **k: [])
    monkeypatch.setattr(ai_secretary, "create_governance_items", lambda *a, **k: ([], []))
    monkeypatch.setattr(ai_secretary, "brief_summary", lambda *a, **k: "Synthetic")
    monkeypatch.setattr(ai_secretary, "configured_action_adapter", lambda *a, **k: SimpleNamespace(provider="test"))


def test_resolve_shared_sync_token_id_is_none_without_any_cohort_row(db_session):
    """The 6 unrelated, independent `GoogleOAuthToken` rows (no mailbox_identity
    generation at all) must resolve to None -- unaffected, unchanged behaviour."""
    org = Organization(name="Independent org"); db_session.add(org); db_session.flush()
    project = Project(name="Independent project", organization_id=org.id)
    db_session.add(project); db_session.flush()
    token = GoogleOAuthToken(project_id=project.id, access_token="a", refresh_token="a")
    db_session.add(token); db_session.commit()

    assert gmail._resolve_shared_sync_token_id(db_session, project.id) is None


def test_resolve_shared_sync_token_id_is_none_for_disabled_only_cohort(db_session, user_factory):
    """A project whose cohort row exists but was never explicitly enabled must
    fall back to its own static token -- matches the pre-fix (buggy) behaviour
    for projects 14/16/24, which this PR intentionally does not change: making
    them use the shared token requires an explicit `join_project_cohort` (an
    operational decision for the owner), not a code change."""
    org = Organization(name="Shared org"); db_session.add(org); db_session.flush()
    project = Project(name="Shared project", organization_id=org.id)
    db_session.add(project); db_session.flush()
    token = GoogleOAuthToken(project_id=project.id, token_uri="https://oauth2.googleapis.com/token")
    db_session.add(token); db_session.flush()
    MailboxIdentityService().bind_verified_google_subject(
        db_session, organization_id=org.id, google_token_id=token.id,
        subject="disabled-cohort-subject", now=NOW)
    db_session.commit()

    cohort = db_session.scalar(select(MailboxProjectCohort).where(MailboxProjectCohort.project_id == project.id))
    assert cohort.enabled is False
    assert gmail._resolve_shared_sync_token_id(db_session, project.id) is None


def test_sweep_uses_shared_current_generation_token_for_enabled_cohort_project(
        db_session, user_factory, monkeypatch):
    """Mirrors the real incident of 09.10.2026: a project enrolled in a shared
    mailbox's cohort (enabled=True) must authenticate the scheduled sweep with
    the CURRENT generation's live token -- belonging to a DIFFERENT project --
    never its own stale GoogleOAuthToken row (which would raise RefreshError,
    exactly like `invalid_grant: Token has been expired or revoked.` in prod).
    The resulting Message must still be filed under the project being synced.
    """
    org = Organization(name="Incident org"); db_session.add(org); db_session.flush()
    stale_project = Project(name="Project 14 analogue", organization_id=org.id)
    live_project = Project(name="Project 17 analogue", organization_id=org.id)
    db_session.add_all([stale_project, live_project]); db_session.flush()
    stale_token = GoogleOAuthToken(project_id=stale_project.id, token_uri="https://oauth2.googleapis.com/token")
    live_token = GoogleOAuthToken(project_id=live_project.id, token_uri="https://oauth2.googleapis.com/token")
    db_session.add_all([stale_token, live_token]); db_session.flush()

    service = MailboxIdentityService()
    service.bind_verified_google_subject(
        db_session, organization_id=org.id, google_token_id=stale_token.id,
        subject="shared-incident-subject", now=NOW)
    stale_cohort = db_session.scalar(select(MailboxProjectCohort).where(
        MailboxProjectCohort.project_id == stale_project.id))
    stale_cohort.enabled = True  # explicit prior join, as project 14/16/24 would need
    db_session.flush()
    # The owner reconnects Gmail from the OTHER project's context -- rotates the
    # shared identity to a new generation whose token belongs to live_project,
    # exactly like project 17's reconnect rotated generation 11 -> 12 tonight.
    service.bind_verified_google_subject(
        db_session, organization_id=org.id, google_token_id=live_token.id,
        subject="shared-incident-subject", now=NOW)
    db_session.commit()

    item = _gmail_item("incident-message-1")

    def fake_mailbox(token_id, db):
        if token_id == stale_token.id:
            raise AssertionError("must not authenticate with the synced project's own stale token")
        assert token_id == live_token.id
        return _fake_gmail_service([item])

    def fake_project(project_id, db):
        if project_id == stale_project.id:
            raise AssertionError("must not fall back to google_workspace_for_project for a cohort project")
        return _fake_gmail_service([])  # live_project's own sweep pass: nothing new

    user_factory(is_admin=True)
    monkeypatch.setattr(gmail.engine.dialect, "name", "sqlite")
    monkeypatch.setattr("app.api.gmail.google_workspace_for_mailbox", fake_mailbox)
    monkeypatch.setattr("app.api.gmail.google_workspace_for_project", fake_project)
    monkeypatch.setattr("app.api.gmail.project_candidate", lambda *a, **k: (stale_project.id, 0.95, "synthetic"))
    monkeypatch.setattr("app.api.gmail.contact_for_sender", lambda *a, **k: None)
    monkeypatch.setattr("app.api.gmail.notify_telegram", lambda *a, **k: None)
    _patch_ai_secretary_engines(monkeypatch)
    monkeypatch.setattr(gmail, "SessionLocal", lambda: nullcontext(db_session))

    totals = gmail.sync_authorized_projects_once()

    assert totals["failed"] == 0
    message = db_session.scalar(select(Message).where(Message.source_external_id == item["id"]))
    assert message is not None
    assert message.project_id == stale_project.id


def test_sweep_shares_one_generation_token_across_multiple_cohort_projects(
        db_session, user_factory, monkeypatch):
    """Two different projects, both enrolled (enabled=True) in the same shared
    mailbox's cohort, both succeed in one sweep pass using the SAME current
    generation token -- belonging to a third, owning project."""
    org = Organization(name="Multi-cohort org"); db_session.add(org); db_session.flush()
    member_one = Project(name="Member one", organization_id=org.id)
    member_two = Project(name="Member two", organization_id=org.id)
    owner_project = Project(name="Current owner", organization_id=org.id)
    db_session.add_all([member_one, member_two, owner_project]); db_session.flush()
    token_one = GoogleOAuthToken(project_id=member_one.id, token_uri="https://oauth2.googleapis.com/token")
    token_two = GoogleOAuthToken(project_id=member_two.id, token_uri="https://oauth2.googleapis.com/token")
    owner_token = GoogleOAuthToken(project_id=owner_project.id, token_uri="https://oauth2.googleapis.com/token")
    db_session.add_all([token_one, token_two, owner_token]); db_session.flush()

    service = MailboxIdentityService()
    service.bind_verified_google_subject(
        db_session, organization_id=org.id, google_token_id=token_one.id,
        subject="shared-multi-subject", now=NOW)
    cohort_one = db_session.scalar(select(MailboxProjectCohort).where(
        MailboxProjectCohort.project_id == member_one.id))
    cohort_one.enabled = True
    db_session.flush()
    service.bind_verified_google_subject(
        db_session, organization_id=org.id, google_token_id=token_two.id,
        subject="shared-multi-subject", now=NOW)
    cohort_two = db_session.scalar(select(MailboxProjectCohort).where(
        MailboxProjectCohort.project_id == member_two.id))
    cohort_two.enabled = True
    db_session.flush()
    service.bind_verified_google_subject(
        db_session, organization_id=org.id, google_token_id=owner_token.id,
        subject="shared-multi-subject", now=NOW)
    db_session.commit()

    assert gmail._resolve_shared_sync_token_id(db_session, member_one.id) == owner_token.id
    assert gmail._resolve_shared_sync_token_id(db_session, member_two.id) == owner_token.id

    items_by_project = {
        member_one.id: _gmail_item("multi-message-one"),
        member_two.id: _gmail_item("multi-message-two"),
    }
    # The sweep visits projects in ascending project_id order (member_one,
    # then member_two, both created -- and so id-assigned -- before
    # owner_project); google_workspace_for_mailbox itself is never told which
    # project is calling (it only ever receives a token id), exactly as in
    # production, so call order is how this fake tells the two apart.
    call_order = iter([member_one.id, member_two.id])

    def fake_mailbox(token_id, db):
        assert token_id == owner_token.id
        return _fake_gmail_service([items_by_project[next(call_order)]])

    def fake_project(project_id, db):
        assert project_id == owner_project.id
        return _fake_gmail_service([])

    user_factory(is_admin=True)
    monkeypatch.setattr(gmail.engine.dialect, "name", "sqlite")
    monkeypatch.setattr("app.api.gmail.google_workspace_for_mailbox", fake_mailbox)
    monkeypatch.setattr("app.api.gmail.google_workspace_for_project", fake_project)
    monkeypatch.setattr("app.api.gmail.project_candidate",
                        lambda db, project_id, *a, **k: (project_id, 0.95, "synthetic"))
    monkeypatch.setattr("app.api.gmail.contact_for_sender", lambda *a, **k: None)
    monkeypatch.setattr("app.api.gmail.notify_telegram", lambda *a, **k: None)
    _patch_ai_secretary_engines(monkeypatch)
    monkeypatch.setattr(gmail, "SessionLocal", lambda: nullcontext(db_session))

    totals = gmail.sync_authorized_projects_once()

    assert totals["failed"] == 0
    # Both cohort members' messages were filed, each under the project whose
    # sweep pass produced it (project_candidate is mocked to the synced
    # project, so pairing with the specific synthetic item id is irrelevant
    # here) -- the point under test is that the SAME shared token authenticated
    # both, in the same sweep pass, without either falling back to its own
    # static token or to the other's project context.
    messages = db_session.scalars(select(Message).where(
        Message.source_external_id.in_(["multi-message-one", "multi-message-two"]))).all()
    assert len(messages) == 2
    assert {m.project_id for m in messages} == {member_one.id, member_two.id}
