"""Offline paged resync: bounded buffers, restart safety and exact worker fences."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app import gmail_history
from app.api import gmail
from app.gmail_history import GmailHistoryUnavailable, bounded_history_resync
from app.models.ai_secretary import Message
from app.models.job import BackgroundJob
from app.models.mailbox_identity import GmailHistoryCheckpoint
from test_mvp2_gmail_history_acceptance import PagedGmail, _install_offline_runtime, _message
from test_mvp2_gmail_history_cursor import checkpoint_world, HistoryService, HttpError


class ResyncGmail(PagedGmail):
    def __init__(self, pages, items=None):
        super().__init__(items or {}, pages)
        self.events = []
        self.profile_id = "200"
        self.after_list = lambda _token: None
        self.after_get = lambda _id: None

    def getProfile(self, **kwargs):
        def execute():
            self.events.append("profile")
            return {"historyId": self.profile_id}
        return SimpleNamespace(execute=execute)

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        token = kwargs.get("pageToken")
        def execute():
            self.events.append(("list", token))
            self.after_list(token)
            return self.pages[token]
        return SimpleNamespace(execute=execute)

    def get(self, **kwargs):
        message_id = kwargs["id"]
        def execute():
            self.after_get(message_id)
            return self.items[message_id]
        return SimpleNamespace(execute=execute)


def test_more_than_100_is_ingested_page_by_page_without_retaining_all_refs():
    provider = ResyncGmail({
        None: {"messages": [{"id": str(i)} for i in range(100)], "nextPageToken": "two"},
        "two": {"messages": [{"id": str(i)} for i in range(100, 200)], "nextPageToken": "three"},
        "three": {"messages": [{"id": "200"}]},
    })
    sizes = []
    def process(refs):
        sizes.append(len(refs))
        provider.events.append(("ingest", len(refs)))
        # Each batch is consumed before requesting the next page. This checks
        # the streaming contract without a timing/allocator-dependent test.
        assert len(provider.list_calls) == len(sizes)
        return {"processed": len(refs), "failed": 0, "private": "not retained"}
    assert bounded_history_resync(provider, process, lambda: None) == (
        "200", {"processed": 201, "failed": 0},
    )
    assert sizes == [100, 100, 1]
    assert all(call["maxResults"] == 100 for call in provider.list_calls)
    assert provider.events[0] == "profile"


def test_empty_pages_with_continuation_and_empty_mailbox_complete():
    provider = ResyncGmail({None: {"nextPageToken": "two"}, "two": {"messages": []}})
    assert bounded_history_resync(provider, lambda _: pytest.fail("empty ingestion"), lambda: None) == (
        "200", {"processed": 0},
    )
    assert len(provider.list_calls) == 2


@pytest.mark.parametrize("token", ["", 42, "x" * 2001, "repeat"])
def test_bad_or_repeated_tokens_stop_before_bad_page_ingestion(token):
    provider = ResyncGmail({None: {"messages": [{"id": "a"}], "nextPageToken": token}})
    if token == "repeat":
        provider.pages[token] = {"messages": [{"id": "b"}], "nextPageToken": token}
    batches = []
    with pytest.raises(ValueError, match="gmail_page_unavailable"):
        bounded_history_resync(provider, lambda refs: batches.append(refs) or {}, lambda: None)
    assert len(batches) == (1 if token == "repeat" else 0)


@pytest.mark.parametrize("messages", [[{"id": "a"}] * 101, [None], [{}], [{"id": "x" * 501}], "invalid"])
def test_oversized_or_malformed_pages_fail_before_ingestion(messages):
    provider = ResyncGmail({None: {"messages": messages}})
    with pytest.raises(ValueError, match="gmail_page_unavailable"):
        bounded_history_resync(provider, lambda _: pytest.fail("bad page ingestion"), lambda: None)


@pytest.mark.parametrize("budget", ["pages", "messages"])
def test_run_budget_stops_unbounded_empty_or_duplicate_provider_pages(monkeypatch, budget):
    monkeypatch.setattr(gmail_history, "MAX_RESYNC_PAGES", 2 if budget == "pages" else 10)
    monkeypatch.setattr(gmail_history, "MAX_RESYNC_MESSAGES", 2 if budget == "messages" else 10000)
    refs = [] if budget == "pages" else [{"id": "same"}]
    provider = ResyncGmail({
        None: {"messages": refs, "nextPageToken": "two"},
        "two": {"messages": refs, "nextPageToken": "three"},
    })
    with pytest.raises(ValueError, match="gmail_resync_incomplete"):
        bounded_history_resync(provider, lambda _: {"processed": 0}, lambda: None)
    assert len(provider.list_calls) == 2


def worker_world(db_session, user_factory, monkeypatch, provider):
    from app import database
    from app.integrations import google_workspace
    from app.jobs import queue
    value, checkpoint = checkpoint_world(db_session, user_factory, history_id=None)
    _install_offline_runtime(monkeypatch, value, provider, [])
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(value.db))
    monkeypatch.setattr(google_workspace, "google_workspace_for_mailbox",
                        lambda *_a, **_k: SimpleNamespace(service=lambda *_a: provider))
    now = datetime.now(timezone.utc)
    job = BackgroundJob(kind="gmail.history.sync", payload={"checkpoint_id": checkpoint.id},
                        status="running", worker_id="resync-worker", attempts=1,
                        locked_at=now, lease_expires_at=now + timedelta(minutes=5))
    value.db.add(job)
    value.db.commit()
    claim = (job.id, job.worker_id, job.attempts, job.locked_at)
    monkeypatch.setattr(queue, "current_execution_claim", lambda: claim)
    return value, checkpoint, job


def run_worker(checkpoint):
    return gmail_history.run_gmail_history_job({"checkpoint_id": checkpoint.id})


def test_mid_page_failure_restarts_from_first_page_and_deduplicates_committed_rows(
        db_session, user_factory, monkeypatch):
    provider = ResyncGmail({
        None: {"messages": [{"id": "a"}, {"id": "a"}], "nextPageToken": "two"},
        "two": {"messages": [{"id": "a"}, {"id": "b"}, {"id": "c"}]},
    }, {key: _message(key, "200") for key in "abc"})
    value, checkpoint, _job = worker_world(db_session, user_factory, monkeypatch, provider)
    def fail_middle(message_id):
        if message_id == "b":
            raise ValueError("synthetic failure")
    provider.after_get = fail_middle
    with pytest.raises(GmailHistoryUnavailable, match="history_ingest_incomplete"):
        run_worker(checkpoint)
    value.db.refresh(checkpoint)
    assert checkpoint.last_history_id is None and checkpoint.status == "resync_required"
    assert value.db.scalar(select(func.count()).select_from(Message).where(
        Message.provider_message_id.in_(list("abc")))) == 2
    provider.after_get = lambda _: None
    result = run_worker(checkpoint)
    assert result == {"mode": "resync", "processed": 1, "skipped": 3, "failed": 0}
    assert [call.get("pageToken") for call in provider.list_calls] == [None, "two", None, "two"]
    assert value.db.scalar(select(func.count()).select_from(Message).where(
        Message.provider_message_id.in_(list("abc")))) == 3
    value.db.refresh(checkpoint)
    assert checkpoint.status == "active" and checkpoint.last_history_id == "200"
    calls = len(provider.list_calls)
    assert run_worker(checkpoint) == result
    assert len(provider.list_calls) == calls


@pytest.mark.parametrize("change", ["generation", "lease", "epoch", "mailbox"])
@pytest.mark.parametrize("boundary", ["list", "get"])
def test_changed_scope_or_lease_stops_before_ingestion_and_next_page(
        db_session, user_factory, monkeypatch, change, boundary):
    provider = ResyncGmail({None: {"messages": [{"id": "a"}], "nextPageToken": "two"},
                            "two": {"messages": []}}, {"a": _message("a", "200")})
    value, checkpoint, job = worker_world(db_session, user_factory, monkeypatch, provider)
    def invalidate(_):
        if change == "generation":
            value.identity.credential_generation += 1
        elif change == "lease":
            job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif change == "epoch":
            value.db.execute(update(GmailHistoryCheckpoint).where(
                GmailHistoryCheckpoint.id == checkpoint.id,
            ).values(checkpoint_epoch=GmailHistoryCheckpoint.checkpoint_epoch + 1)
              .execution_options(synchronize_session=False))
        else:
            value.identity.binding_epoch += 1
        value.db.commit()
    if boundary == "list":
        provider.after_list = invalidate
    else:
        provider.after_get = invalidate
    with pytest.raises(GmailHistoryUnavailable):
        run_worker(checkpoint)
    value.db.refresh(checkpoint)
    assert checkpoint.last_history_id is None
    assert value.db.scalar(select(func.count()).select_from(Message).where(
        Message.provider_message_id == "a")) == 0
    assert len(provider.list_calls) == 1


def test_mail_arriving_during_resync_is_read_from_prescan_history_pin(
        db_session, user_factory, monkeypatch):
    provider = ResyncGmail({None: {"messages": [{"id": "a"}]}}, {"a": _message("a", "200")})
    value, checkpoint, _job = worker_world(db_session, user_factory, monkeypatch, provider)
    provider.after_list = lambda _: setattr(provider, "profile_id", "205")
    run_worker(checkpoint)
    value.db.refresh(checkpoint)
    assert checkpoint.last_history_id == "200"
    history = HistoryService({None: {"history": [{"messages": [{"id": "arrived"}]}],
                                    "historyId": "205"}})
    seen = []
    gmail_history.run_history_sync(value.db, checkpoint_id=checkpoint.id, job_id=999,
        service=history, process_refs=lambda refs: seen.extend(refs) or {"processed": len(refs)},
        full_resync=lambda: pytest.fail("must remain incremental"))
    assert seen == [{"id": "arrived"}]
    assert history.users_resource.history_resource.calls[0]["startHistoryId"] == "200"


def test_worker_ingests_101_messages_and_duplicate_page_once_per_mailbox(
        db_session, user_factory, monkeypatch):
    ids = [f"paged-{i}" for i in range(101)]
    first = [{"id": key} for key in ids[:100]]
    provider = ResyncGmail({
        None: {"messages": first, "nextPageToken": "duplicate"},
        "duplicate": {"messages": first, "nextPageToken": "last"},
        "last": {"messages": [{"id": ids[-1]}]},
    }, {key: _message(key, "200") for key in ids})
    value, checkpoint, _job = worker_world(db_session, user_factory, monkeypatch, provider)
    result = run_worker(checkpoint)
    assert result == {"mode": "resync", "processed": 101, "skipped": 100, "failed": 0}
    assert value.db.scalar(select(func.count()).select_from(Message).where(
        Message.provider_message_id.in_(ids))) == 101
    value.db.refresh(checkpoint)
    assert checkpoint.last_history_id == "200" and checkpoint.status == "active"


def test_lease_recovery_after_first_page_replays_from_start(
        db_session, user_factory, monkeypatch):
    from app.jobs import queue
    provider = ResyncGmail({None: {"messages": [{"id": "a"}], "nextPageToken": "two"},
                           "two": {"messages": [{"id": "b"}]}},
                          {key: _message(key, "200") for key in "ab"})
    value, checkpoint, job = worker_world(db_session, user_factory, monkeypatch, provider)
    def expire_second_page(token):
        if token == "two":
            job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            value.db.commit()
    provider.after_list = expire_second_page
    with pytest.raises(GmailHistoryUnavailable):
        run_worker(checkpoint)
    value.db.refresh(checkpoint)
    assert checkpoint.last_history_id is None and checkpoint.status == "syncing"
    assert value.db.scalar(select(func.count()).select_from(Message).where(
        Message.provider_message_id.in_(list("ab")))) == 1
    job.attempts += 1
    job.locked_at = datetime.now(timezone.utc)
    job.lease_expires_at = job.locked_at + timedelta(minutes=5)
    value.db.commit()
    claim = (job.id, job.worker_id, job.attempts, job.locked_at)
    monkeypatch.setattr(queue, "current_execution_claim", lambda: claim)
    provider.after_list = lambda _: None
    assert run_worker(checkpoint) == {"mode": "resync", "processed": 1, "skipped": 1, "failed": 0}
    assert [call.get("pageToken") for call in provider.list_calls] == [None, "two", None, "two"]


def test_expired_incremental_cursor_uses_updated_epoch_guard_during_resync(
        db_session, user_factory, monkeypatch):
    provider = ResyncGmail({None: {"messages": [{"id": "a"}]}}, {"a": _message("a", "200")})
    provider.history = lambda: HistoryService({None: HttpError(404)}).users().history()
    value, checkpoint, _job = worker_world(db_session, user_factory, monkeypatch, provider)
    checkpoint.status = "active"
    checkpoint.last_history_id = "100"
    value.db.commit()
    assert run_worker(checkpoint)["processed"] == 1
    value.db.refresh(checkpoint)
    assert checkpoint.last_history_id == "200" and checkpoint.checkpoint_epoch == 4


def test_budget_exhaustion_preserves_cursor_and_committed_rows(
        db_session, user_factory, monkeypatch):
    monkeypatch.setattr(gmail_history, "MAX_RESYNC_MESSAGES", 1)
    provider = ResyncGmail({None: {"messages": [{"id": "a"}], "nextPageToken": "two"},
                           "two": {"messages": [{"id": "b"}]}},
                          {key: _message(key, "200") for key in "ab"})
    value, checkpoint, _job = worker_world(db_session, user_factory, monkeypatch, provider)
    with pytest.raises(GmailHistoryUnavailable):
        run_worker(checkpoint)
    value.db.refresh(checkpoint)
    assert checkpoint.status == "resync_required" and checkpoint.last_history_id is None
    assert value.db.scalar(select(func.count()).select_from(Message).where(
        Message.provider_message_id == "a")) == 1
    assert len(provider.list_calls) == 1
    # This test deliberately raises the fixture's policy budget. Automatic
    # retries at the same exhausted limit do not claim to recover a large inbox.
    monkeypatch.setattr(gmail_history, "MAX_RESYNC_MESSAGES", 2)
    assert run_worker(checkpoint) == {"mode": "resync", "processed": 1, "skipped": 1, "failed": 0}
    assert [call["maxResults"] for call in provider.list_calls] == [1, 2, 1]
