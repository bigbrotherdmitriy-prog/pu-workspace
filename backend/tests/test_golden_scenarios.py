"""Golden end-to-end scenarios across the mail, sync-health and job layers.

Each test drives the real product code from the first input to the last
durable effect; only the outside world (Gmail API, AI provider, Telegram) is
replaced with in-process fakes. No network.
"""
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import gmail_sync_health
from app.api import gmail, responses
from app.automations import gmail as gmail_automation
from app.integrations import ai as ai_adapter
from app.integrations.contracts import AIProviderAdapter
from app.jobs.handlers import run
from app.jobs.queue import claim, enqueue, fail, succeed
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_contact import ProjectContact
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.v54_provider_action import ProviderAction

LETTER = ("Добрый день! Просим направить акт сверки до 20.10.2026. "
          "Прошу подтвердить получение письма.")


def _gmail_item(gmail_id="gmail-golden-1", text=LETTER):
    return {"id": gmail_id, "threadId": "thread-golden-1", "labelIds": ["INBOX"],
            "payload": {"mimeType": "text/plain", "headers": [
                {"name": "Subject", "value": "Акт сверки"},
                {"name": "From", "value": "Client <client@example.test>"},
                {"name": "To", "value": "owner@example.test"}],
                "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}}


class FakeGmail:
    """Gmail API read surface; any write (send/modify/trash) would fail the test."""

    def __init__(self, items):
        self.items = {item["id"]: item for item in items}
        self.writes = []

    def users(self): return self
    def messages(self): return self

    def list(self, **_kwargs):
        return SimpleNamespace(execute=lambda: {"messages": [{"id": key} for key in self.items]})

    def get(self, *, id, **_kwargs):
        item = self.items[id]
        if isinstance(item, Exception):
            def broken(): raise item
            return SimpleNamespace(execute=broken)
        return SimpleNamespace(execute=lambda: item)

    def __getattr__(self, name):
        if name in {"send", "modify", "trash", "delete", "insert", "batchModify"}:
            def write(**kwargs):
                self.writes.append((name, kwargs))
                raise AssertionError(f"Gmail {name} must not be called")
            return write
        raise AttributeError(name)


@pytest.fixture
def mail_world(db_session, user_factory, monkeypatch):
    db = db_session
    owner = user_factory(email="owner@example.test")
    organization = Organization(name="Golden organization")
    db.add(organization); db.flush()
    project = Project(name="Golden project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
    db.add(GoogleOAuthToken(project_id=project.id, access_token=None, refresh_token=None,
                            scopes="https://www.googleapis.com/auth/gmail.send"))
    # A confirmed client contact routes the letter to the project without review.
    db.add(ProjectContact(organization_id=organization.id, project_id=project.id, created_by_user_id=owner.id,
                          name="Client", email="client@example.test", normalized_email="client@example.test",
                          confirmed=True, active=True, source="manual"))
    db.commit()
    provider = Mock(spec=AIProviderAdapter)
    provider.analyze_message.return_value = {}
    provider.analyze_document.return_value = {}
    monkeypatch.setattr(ai_adapter, "configured_ai_provider", lambda: provider)
    telegram = []
    monkeypatch.setattr(gmail, "notify_telegram", lambda *args: telegram.append(args))
    return SimpleNamespace(db=db, owner=owner, project=project, telegram=telegram)


def _sync(world, monkeypatch, fake):
    monkeypatch.setattr(gmail, "google_workspace_for_project",
                        lambda *_: SimpleNamespace(service=lambda *_: fake))
    return gmail.sync_gmail_project(world.project.id, world.db, world.owner,
                                    query="is:inbox newer_than:7d", max_results=25)


def _dispatch_jobs(db):
    return list(db.scalars(select(BackgroundJob).where(BackgroundJob.kind == "provider.action.dispatch")))


# --- Scenario 1: incoming letter -> task + draft -> approval -> queued, never sent before ---


def test_incoming_letter_becomes_task_and_draft_and_is_only_queued_after_approval(mail_world, monkeypatch):
    db, owner = mail_world.db, mail_world.owner
    fake = FakeGmail([_gmail_item()])

    result = _sync(mail_world, monkeypatch, fake)

    assert result["processed"] == 1 and result["failed"] == 0
    message = db.scalar(select(Message))
    assert message.source_external_id == "gmail-golden-1" and message.context_confirmed
    task = db.scalar(select(Task).where(Task.message_id == message.id))
    assert task is not None and task.status == "assigned"
    assert task.external_action_status == "proposed"          # not pushed to Google Tasks
    drafts = list(db.scalars(select(ResponseDraft).where(ResponseDraft.message_id == message.id)))
    assert drafts and all(d.status == "draft" and d.approved_revision is None for d in drafts)
    draft = drafts[0]

    # Without approval nothing can be queued, let alone sent.
    with pytest.raises(HTTPException) as unapproved:
        gmail.send_gmail(draft.id, db, owner)
    assert unapproved.value.status_code == 409
    assert _dispatch_jobs(db) == [] and db.scalar(select(func.count()).select_from(ProviderAction)) == 0

    approved = responses.update_draft(draft.id, responses.DraftUpdate(status="approved"), db, owner)
    assert approved["status"] == "approved" and approved["approved_revision"] == approved["revision"]

    queued = gmail.send_gmail(draft.id, db, owner)

    assert queued["status"] == "queued" and queued["gmail_message_id"] is None
    assert queued["already_sent"] is False
    db.refresh(draft)
    assert draft.status == "queued" and draft.sent_external_id is None
    [job] = _dispatch_jobs(db)
    assert job.status == "queued" and job.attempts == 0      # waits for the worker, not sent inline
    action = db.scalar(select(ProviderAction))
    assert action is not None
    # The provider was never written to during the whole flow.
    assert fake.writes == []
    # A repeated click re-uses the same queued action.
    again = gmail.send_gmail(draft.id, db, owner)
    assert again["status"] == "queued"
    assert len(_dispatch_jobs(db)) == 1


def test_editing_an_approved_draft_withdraws_the_approval(mail_world, monkeypatch):
    db, owner = mail_world.db, mail_world.owner
    _sync(mail_world, monkeypatch, FakeGmail([_gmail_item()]))
    draft = db.scalar(select(ResponseDraft))
    responses.update_draft(draft.id, responses.DraftUpdate(status="approved"), db, owner)

    responses.update_draft(draft.id, responses.DraftUpdate(body="Изменённый текст ответа."), db, owner)

    db.refresh(draft)
    assert draft.status == "draft" and draft.approved_revision is None
    with pytest.raises(HTTPException) as stale:
        gmail.send_gmail(draft.id, db, owner)
    assert stale.value.status_code == 409
    assert _dispatch_jobs(db) == []


# --- Scenario 2: mail sync keeps failing -> sync-health signal -> one owner alert ---


@pytest.fixture
def sweep(mail_world, monkeypatch):
    """Run the real background sweep (gmail.sync job handler) against the test DB."""
    from contextlib import nullcontext
    from sqlalchemy.orm import sessionmaker

    db = mail_world.db
    monkeypatch.setattr(gmail_automation, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(gmail_automation.engine.dialect, "name", "sqlite")
    sessions = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    alerts = []
    monkeypatch.setattr(gmail_sync_health, "telegram_configured", lambda: True)
    monkeypatch.setattr(gmail_sync_health, "notify_telegram", lambda text: alerts.append(text) or True)

    def check_health(now=None):
        sent = gmail_sync_health.notify_gmail_sync_health_once(
            sessions=sessions, now=now or datetime.now(timezone.utc))
        db.expire_all()
        return sent

    return SimpleNamespace(run=lambda: run("gmail.sync", {}), check_health=check_health, alerts=alerts)


def _health_audits(db, project_id):
    return list(db.scalars(select(AuditLog).where(
        AuditLog.action == gmail_sync_health.ALERT_AUDIT_ACTION, AuditLog.entity_id == project_id)))


def test_letters_failing_on_every_sweep_raise_one_owner_alert(mail_world, monkeypatch, sweep):
    db, project = mail_world.db, mail_world.project
    # Every letter of the inbox breaks while being read (e.g. a provider 500).
    _sync_service = FakeGmail([_gmail_item("gmail-broken-1"), _gmail_item("gmail-broken-2")])
    _sync_service.items = {key: RuntimeError("synthetic provider failure") for key in _sync_service.items}
    monkeypatch.setattr(gmail, "google_workspace_for_project",
                        lambda *_: SimpleNamespace(service=lambda *_: _sync_service))

    results = [sweep.run() for _ in range(gmail_sync_health.STREAK - 1)]
    assert all(r["processed"] == 0 and r["failed"] == 2 for r in results)
    # Two failing sweeps are not yet a signal.
    assert sweep.check_health() == 0 and sweep.alerts == []

    sweep.run()
    audits = list(db.scalars(select(AuditLog).where(AuditLog.action == "gmail_sync",
                                                    AuditLog.entity_id == project.id)))
    assert len(audits) == gmail_sync_health.STREAK
    assert all("processed=0" in a.details and "failed=2" in a.details and "errors=RuntimeError" in a.details
               for a in audits)
    assert db.scalar(select(func.count()).select_from(Message)) == 0

    assert sweep.check_health() == 1
    [alert] = sweep.alerts
    assert project.name in alert and "RuntimeError" in alert
    assert [a.details for a in _health_audits(db, project.id)] == [
        f"kind=ingest;date={datetime.now(timezone.utc).date().isoformat()}"]
    # The signal is raised once per project, kind and day, not on every check.
    sweep.run()
    assert sweep.check_health() == 0 and len(sweep.alerts) == 1


def test_revoked_mailbox_access_raises_a_connection_alert(mail_world, monkeypatch, sweep):
    db, project = mail_world.db, mail_world.project

    def revoked(*_args, **_kwargs):
        raise PermissionError("synthetic: Google token revoked")

    monkeypatch.setattr(gmail, "google_workspace_for_project", revoked)
    for _ in range(gmail_sync_health.STREAK):
        assert sweep.run()["failed"] == 1

    failures = list(db.scalars(select(AuditLog).where(AuditLog.action == "gmail_auto_sync_failed",
                                                      AuditLog.entity_id == project.id)))
    assert len(failures) == gmail_sync_health.STREAK
    assert sweep.check_health() == 1
    assert "не удаётся подключиться" in sweep.alerts[0]
    assert _health_audits(db, project.id)[0].details.startswith("kind=connection;")


def test_a_healthy_sweep_in_between_keeps_quiet(mail_world, monkeypatch, sweep):
    broken = FakeGmail([_gmail_item("gmail-broken-1")])
    broken.items = {"gmail-broken-1": RuntimeError("synthetic provider failure")}
    healthy = FakeGmail([_gmail_item("gmail-ok-1")])
    for service in (broken, healthy, broken):
        monkeypatch.setattr(gmail, "google_workspace_for_project",
                            lambda *_, s=service: SimpleNamespace(service=lambda *_: s))
        sweep.run()
    assert sweep.check_health() == 0 and sweep.alerts == []


# --- Scenario 3: a job that mostly failed must not look "completed" (13.10 incident) ---


def _finish(db, kind, result, worker="golden-worker"):
    """Claim a job and finish it exactly as app.jobs.worker does after run()."""
    job = enqueue(db, kind, {})
    claimed = claim(db, worker)
    assert claimed.id == job.id
    assert succeed(db, job.id, worker, result)
    db.refresh(job)
    return job


@pytest.mark.parametrize("kind,result", [
    ("gmail.sync", {"projects": 1, "processed": 1, "skipped": 0, "failed": 2, "overlap_skipped": 0}),
    ("gmail.sync", {"projects": 1, "processed": 0, "skipped": 4, "failed": 3, "overlap_skipped": 0}),
    ("ai.rules", {"due": 3, "prepared": 1, "failed": 2}),
    ("notifications.refresh", {"projects": 2, "members": 1, "failed": 4}),
])
def test_job_with_most_items_failed_is_not_plain_completed(db_session, kind, result):
    job = _finish(db_session, kind, result)
    assert job.status == "failed"
    assert job.last_error == "MajorityOfItemsFailed"
    assert job.result == result                    # the counts stay visible to the operator
    assert job.completed_at is not None


@pytest.mark.parametrize("result", [
    {"projects": 1, "processed": 3, "skipped": 0, "failed": 1, "overlap_skipped": 0},
    {"projects": 1, "processed": 1, "skipped": 0, "failed": 1, "overlap_skipped": 0},   # exactly half
    {"projects": 0, "processed": 0, "skipped": 0, "failed": 0, "overlap_skipped": 1},   # nothing to do
    {"snapshot_id": 7},                                                                  # no item counts
])
def test_job_with_most_items_done_stays_completed(db_session, result):
    job = _finish(db_session, "gmail.sync", result)
    assert job.status == "completed" and job.last_error is None


def test_real_failing_sweep_ends_failed_in_the_queue(mail_world, monkeypatch, sweep):
    """The 13.10 shape end to end: the sweep job runs, every letter fails, the job is not 'completed'."""
    db = mail_world.db
    broken = FakeGmail([_gmail_item("gmail-broken-1"), _gmail_item("gmail-broken-2")])
    broken.items = {key: RuntimeError("synthetic provider failure") for key in broken.items}
    monkeypatch.setattr(gmail, "google_workspace_for_project",
                        lambda *_: SimpleNamespace(service=lambda *_: broken))
    job = enqueue(db, "gmail.sync", {})
    claimed = claim(db, "golden-worker")
    result = run(claimed.kind, dict(claimed.payload))
    assert result["processed"] == 0 and result["failed"] == 2
    succeed(db, job.id, "golden-worker", result)
    db.refresh(job)
    assert job.status == "failed" and job.last_error == "MajorityOfItemsFailed"
