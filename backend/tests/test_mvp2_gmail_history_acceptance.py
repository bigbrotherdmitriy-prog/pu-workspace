"""Synthetic Gmail history/pagination acceptance without provider I/O."""

import base64
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api import ai_secretary, gmail
from app.api.gmail import _gmail_message_refs
from app.models.ai_secretary import Message
from app.models.mailbox_identity import (
    MailboxCutoverFlags,
    MailboxOriginBinding,
    MailboxOriginCurrent,
)
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.v54_pilot import SourceCurrent, SourceReference, SourceVersion
from test_v54_mailbox_identity import enable_rollout, world


def _message(message_id: str, history_id: str, *, thread_id: str = "thread-shared",
             outgoing: bool = False) -> dict:
    body = base64.urlsafe_b64encode(b"Synthetic low confidence correspondence").decode()
    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": history_id,
        "labelIds": ["SENT" if outgoing else "INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Synthetic ambiguous subject"},
                {"name": "From", "value": "sender@example.test"},
                {"name": "To", "value": "recipient@example.test"},
            ],
            "body": {"data": body},
        },
    }


class PagedGmail:
    def __init__(self, items: dict[str, dict], pages: dict[str | None, dict]):
        self.items = items
        self.pages = pages
        self.list_calls: list[dict] = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        token = kwargs.get("pageToken")
        return SimpleNamespace(execute=lambda: self.pages[token])

    def get(self, **kwargs):
        return SimpleNamespace(execute=lambda: self.items[kwargs["id"]])


def _install_offline_runtime(monkeypatch, w, provider: PagedGmail, draft_calls: list[int]):
    monkeypatch.setattr(
        gmail,
        "google_workspace_for_project",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("project_token_fallback")),
    )
    monkeypatch.setattr(
        gmail,
        "google_workspace_for_mailbox",
        lambda *_a, **_k: SimpleNamespace(service=lambda *_a, **_k: provider),
    )
    monkeypatch.setattr(gmail, "project_candidate", lambda *_a, **_k: (w.project.id, 0.4, "ambiguous"))
    monkeypatch.setattr(gmail, "contact_for_sender", lambda *_a, **_k: None)
    monkeypatch.setattr(gmail, "notify_telegram", lambda *_a, **_k: None)
    monkeypatch.setattr(ai_secretary, "create_tasks_from_files", lambda *_a, **_k: [])
    monkeypatch.setattr(ai_secretary, "create_response_drafts", lambda *_a, **_k: [])
    monkeypatch.setattr(ai_secretary, "create_governance_items", lambda *_a, **_k: ([], []))
    monkeypatch.setattr(ai_secretary, "brief_summary", lambda *_a, **_k: "Synthetic")
    monkeypatch.setattr(
        gmail,
        "create_response_drafts",
        lambda *_a, **_k: draft_calls.append(1) or [],
    )


def test_paginated_history_keeps_exact_mailbox_origin_and_low_confidence_review(
        db_session, user_factory, monkeypatch):
    w = world(db_session, user_factory)
    flags = w.db.scalar(select(MailboxCutoverFlags))
    enable_rollout(flags, "pilot_write")
    w.db.commit()

    first = _message("page-one-message", "history-1", outgoing=True)
    second = _message("page-two-message", "history-2")
    provider = PagedGmail(
        {first["id"]: first, second["id"]: second},
        {
            None: {"messages": [{"id": first["id"]}], "nextPageToken": "opaque-next"},
            "opaque-next": {"messages": [{"id": second["id"]}]},
        },
    )
    draft_calls: list[int] = []
    _install_offline_runtime(monkeypatch, w, provider, draft_calls)

    result = gmail.sync_gmail_project(
        w.project.id, w.db, w.user, query="synthetic-history", max_results=2,
    )

    assert result == {"processed": 2, "skipped": 0, "failed": 0, "errors": []}
    assert provider.list_calls == [
        {"userId": "me", "q": "synthetic-history", "maxResults": 2},
        {"userId": "me", "q": "synthetic-history", "maxResults": 1,
         "pageToken": "opaque-next"},
    ]
    messages = list(w.db.scalars(select(Message).where(
        Message.provider_message_id.in_((first["id"], second["id"])),
    ).order_by(Message.provider_message_id)))
    assert len(messages) == 2
    assert {row.mail_connection_id for row in messages} == {w.mail.id}
    assert {row.source_thread_id for row in messages} == {"thread-shared"}
    assert all(not row.context_confirmed for row in messages)
    assert all(row.status == "needs_context_confirmation" for row in messages)
    assert not draft_calls
    assert not list(w.db.scalars(select(Task).where(Task.message_id.in_([row.id for row in messages]))))

    for row in messages:
        source = w.db.get(SourceReference, row.source_reference_id)
        current = w.db.get(SourceCurrent, source.id)
        origin = w.db.get(MailboxOriginCurrent, row.id)
        assert source.identity_id == w.identity.id
        assert source.canonical_locator == {
            "kind": "gmail_message",
            "provider_message_id": row.provider_message_id,
            "provider_thread_id": "thread-shared",
        }
        assert current is not None and origin is not None


def test_replayed_history_observation_is_append_only_and_does_not_bypass_review(
        db_session, user_factory, monkeypatch):
    w = world(db_session, user_factory)
    flags = w.db.scalar(select(MailboxCutoverFlags))
    enable_rollout(flags, "pilot_write")
    w.db.commit()
    item = _message("history-message", "history-before")
    provider = PagedGmail(
        {item["id"]: item},
        {None: {"messages": [{"id": item["id"]}]}},
    )
    draft_calls: list[int] = []
    _install_offline_runtime(monkeypatch, w, provider, draft_calls)

    first = gmail.sync_gmail_project(
        w.project.id, w.db, w.user, query="synthetic-history", max_results=1,
    )
    row = w.db.scalar(select(Message).where(Message.provider_message_id == item["id"]))
    source_id = row.source_reference_id
    origin_before = w.db.get(MailboxOriginCurrent, row.id)
    binding_before = origin_before.binding_id
    item["historyId"] = "history-after"
    second = gmail.sync_gmail_project(
        w.project.id, w.db, w.user, query="synthetic-history", max_results=1,
    )

    assert first["processed"] == 1 and second["skipped"] == 1
    assert len(list(w.db.scalars(select(Message).where(
        Message.provider_message_id == item["id"],
    )))) == 1
    observations = list(w.db.scalars(select(SourceVersion).where(
        SourceVersion.source_id == source_id,
    ).order_by(SourceVersion.observed_at, SourceVersion.id)))
    assert {value.observation_key for value in observations} == {
        "history-before", "history-after",
    }
    current = w.db.get(SourceCurrent, source_id)
    assert current.version_id == next(
        value.id for value in observations if value.observation_key == "history-after"
    )
    assert row.context_confirmed is False
    assert row.status == "needs_context_confirmation"
    origin_after = w.db.get(MailboxOriginCurrent, row.id)
    assert origin_after.binding_id != binding_before
    assert w.db.get(MailboxOriginBinding, origin_after.binding_id).revision == 2
    assert row.mail_connection_id == w.mail.id and row.source_reference_id == source_id
    assert not draft_calls
    assert w.db.scalar(select(ResponseDraft).where(ResponseDraft.message_id == row.id)) is None


def test_repeated_provider_page_cursor_fails_closed():
    provider = PagedGmail(
        {},
        {
            None: {"messages": [{"id": "one"}], "nextPageToken": "repeat"},
            "repeat": {"messages": [{"id": "two"}], "nextPageToken": "repeat"},
        },
    )
    with pytest.raises(ValueError, match="gmail_page_unavailable"):
        list(_gmail_message_refs(provider, query="synthetic", max_results=3))
    assert len(provider.list_calls) == 2
