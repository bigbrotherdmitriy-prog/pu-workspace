"""Offline Gmail provider fault acceptance; no provider endpoint is contacted."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import gmail
from app.integrations.google_retry import GoogleReadError, execute_google_read
from app.integrations.google_workspace import GoogleWorkspaceAdapter
from app.mailbox_identity.service import MailboxIdentityService
from app.models.ai_secretary import Message
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import MailboxCutoverFlags
from app.models.response_draft import ResponseDraft
from app.staging.gmail import (
    GmailAttachmentDenied,
    GmailProviderDownloadAdapter,
)
from test_mvp2_gmail_history_acceptance import (
    PagedGmail,
    _install_offline_runtime,
    _message,
)
from test_v54_mailbox_identity import enable_rollout, world


class SyntheticHttpError(RuntimeError):
    def __init__(self, status: int, *, retry_after: str | None = None):
        super().__init__("private provider response must not escape")
        headers = {"retry-after": retry_after} if retry_after is not None else {}
        self.resp = SimpleNamespace(status=status, headers=headers)


class FlakyPagedGmail(PagedGmail):
    def __init__(self, items, pages, failures):
        super().__init__(items, pages)
        self.failures = list(failures)

    def list(self, **kwargs):
        self.list_calls.append(kwargs)

        def execute():
            if self.failures:
                raise self.failures.pop(0)
            return self.pages[kwargs.get("pageToken")]

        return SimpleNamespace(execute=execute)


class TokenFaultGmail(PagedGmail):
    def __init__(self, items, pages, *, list_error_by_token=None, get_error=None):
        super().__init__(items, pages)
        self.list_error_by_token = list_error_by_token or {}
        self.get_error = get_error
        self.get_calls = 0

    def list(self, **kwargs):
        self.list_calls.append(kwargs)

        def execute():
            error = self.list_error_by_token.get(kwargs.get("pageToken"))
            if error:
                raise error
            return self.pages[kwargs.get("pageToken")]

        return SimpleNamespace(execute=execute)

    def get(self, **kwargs):
        self.get_calls += 1
        if self.get_error:
            return SimpleNamespace(execute=lambda: (_ for _ in ()).throw(self.get_error))
        return super().get(**kwargs)


def _pilot_world(db_session, user_factory):
    value = world(db_session, user_factory)
    flags = value.db.scalar(select(MailboxCutoverFlags))
    enable_rollout(flags, "pilot_write")
    value.db.commit()
    return value


def test_rate_limit_retries_with_backoff_and_ingests_one_message_once(
        db_session, user_factory, monkeypatch):
    value = _pilot_world(db_session, user_factory)
    item = _message("rate-limited-message", "history-rate-limit")
    provider = FlakyPagedGmail(
        {item["id"]: item},
        {None: {"messages": [{"id": item["id"]}]}},
        [SyntheticHttpError(429)],
    )
    draft_calls: list[int] = []
    _install_offline_runtime(monkeypatch, value, provider, draft_calls)
    delays: list[float] = []
    monkeypatch.setattr("app.integrations.google_retry.time.sleep", delays.append)

    result = gmail.sync_gmail_project(
        value.project.id, value.db, value.user,
        query="synthetic-rate-limit", max_results=1,
    )

    assert result["processed"] == 1 and result["failed"] == 0
    assert len(provider.list_calls) == 2
    assert delays == [pytest.approx(0.25)]
    assert len(list(value.db.scalars(select(Message).where(
        Message.provider_message_id == item["id"],
    )))) == 1
    assert not draft_calls


def test_google_read_backoff_is_bounded_and_hides_provider_response(monkeypatch):
    attempts = []
    delays: list[float] = []
    failures = [SyntheticHttpError(503), SyntheticHttpError(503)]

    def request():
        attempts.append(1)

        def execute():
            if failures:
                raise failures.pop(0)
            return {"safe": True}

        return SimpleNamespace(execute=execute)

    monkeypatch.setattr("app.integrations.google_retry.time.sleep", delays.append)
    assert execute_google_read(request) == {"safe": True}
    assert len(attempts) == 3 and delays == [pytest.approx(0.25), pytest.approx(0.5)]

    private_error = SyntheticHttpError(429, retry_after="1.5")
    with pytest.raises(GoogleReadError) as caught:
        execute_google_read(
            lambda: SimpleNamespace(execute=lambda: (_ for _ in ()).throw(private_error)),
            max_attempts=2,
        )
    assert caught.value.code == "provider_read_unavailable"
    assert caught.value.retryable is True
    assert "private provider response" not in str(caught.value)
    assert delays[-1] == pytest.approx(1.5)


@pytest.mark.parametrize("status", (400, 401, 403))
def test_invalid_cursor_or_expired_token_is_not_retried(status, monkeypatch):
    calls = []
    delays = []
    private_error = SyntheticHttpError(status)
    monkeypatch.setattr("app.integrations.google_retry.time.sleep", delays.append)
    with pytest.raises(GoogleReadError) as caught:
        execute_google_read(lambda: calls.append(1) or SimpleNamespace(
            execute=lambda: (_ for _ in ()).throw(private_error),
        ))
    assert caught.value.code == "provider_read_rejected"
    assert caught.value.retryable is False
    assert calls == [1] and delays == []
    assert "private provider response" not in str(caught.value)


def test_sync_endpoint_maps_provider_page_failure_to_safe_503(
        db_session, user_factory, monkeypatch):
    value = _pilot_world(db_session, user_factory)
    monkeypatch.setattr(gmail, "require_project_role", lambda *_a, **_k: None)
    monkeypatch.setattr(
        gmail,
        "sync_gmail_project",
        lambda *_a, **_k: (_ for _ in ()).throw(
            GoogleReadError("provider_read_rejected", retryable=False)
        ),
    )
    with pytest.raises(HTTPException) as caught:
        gmail.sync_gmail(
            value.project.id, gmail.GmailSyncRequest(), value.db, value.user,
        )
    assert caught.value.status_code == 503
    assert caught.value.detail == "Gmail synchronization is temporarily unavailable"
    assert "provider" not in caught.value.detail.casefold()


def test_expired_second_page_can_be_replayed_without_message_or_draft_duplicate(
        db_session, user_factory, monkeypatch):
    value = _pilot_world(db_session, user_factory)
    first = _message("cursor-first", "history-cursor-first")
    second = _message("cursor-second", "history-cursor-second")
    pages = {
        None: {"messages": [{"id": first["id"]}], "nextPageToken": "expired-cursor"},
        "expired-cursor": {"messages": [{"id": second["id"]}]},
    }
    failed_provider = TokenFaultGmail(
        {first["id"]: first, second["id"]: second}, pages,
        list_error_by_token={"expired-cursor": SyntheticHttpError(400)},
    )
    draft_calls: list[int] = []
    _install_offline_runtime(monkeypatch, value, failed_provider, draft_calls)
    with pytest.raises(GoogleReadError, match="provider_read_rejected"):
        gmail.sync_gmail_project(
            value.project.id, value.db, value.user,
            query="synthetic-expired-cursor", max_results=2,
        )
    assert len(list(value.db.scalars(select(Message).where(
        Message.provider_message_id == first["id"],
    )))) == 1

    replay_provider = TokenFaultGmail(
        {first["id"]: first, second["id"]: second}, pages,
    )
    _install_offline_runtime(monkeypatch, value, replay_provider, draft_calls)
    result = gmail.sync_gmail_project(
        value.project.id, value.db, value.user,
        query="synthetic-expired-cursor", max_results=2,
    )
    assert result["processed"] == 1 and result["skipped"] == 1
    rows = list(value.db.scalars(select(Message).where(
        Message.provider_message_id.in_((first["id"], second["id"])),
    )))
    assert len(rows) == 2 and not draft_calls
    assert not list(value.db.scalars(select(ResponseDraft).where(
        ResponseDraft.message_id.in_([row.id for row in rows]),
    )))


def test_token_expiry_during_message_read_fails_item_without_retry_or_partial_message(
        db_session, user_factory, monkeypatch):
    value = _pilot_world(db_session, user_factory)
    item = _message("expired-token-message", "history-expired-token")
    provider = TokenFaultGmail(
        {item["id"]: item},
        {None: {"messages": [{"id": item["id"]}]}},
        get_error=SyntheticHttpError(401),
    )
    draft_calls: list[int] = []
    _install_offline_runtime(monkeypatch, value, provider, draft_calls)
    result = gmail.sync_gmail_project(
        value.project.id, value.db, value.user,
        query="synthetic-expired-token", max_results=1,
    )
    assert result == {
        "processed": 0,
        "skipped": 0,
        "failed": 1,
        "errors": [{"item_index": 1, "error": "GoogleReadError"}],
    }
    assert provider.get_calls == 1
    assert value.db.scalar(select(Message).where(
        Message.provider_message_id == item["id"],
    )) is None


def test_expired_credentials_refresh_once_and_persist_new_access_token(
        db_session, user_factory, monkeypatch):
    value = _pilot_world(db_session, user_factory)
    token = value.db.get(GoogleOAuthToken, value.token.id)
    token.access_token = "sealed-old-access"
    token.refresh_token = "sealed-refresh"
    value.db.commit()
    refresh_calls = []

    class Credentials:
        expired = True
        token = "old-access"
        refresh_token = "refresh"

        def refresh(self, request):
            refresh_calls.append(request)
            self.token = "new-access"

    monkeypatch.setattr(
        "app.integrations.google_workspace.Credentials",
        lambda **_kwargs: Credentials(),
    )
    monkeypatch.setattr("app.integrations.google_workspace.Request", lambda: "safe-request")
    monkeypatch.setattr(
        "app.integrations.google_workspace.decrypt_token",
        lambda value: {"sealed-old-access": "old-access", "sealed-refresh": "refresh"}[value],
    )
    monkeypatch.setattr(
        "app.integrations.google_workspace.encrypt_token",
        lambda value: f"sealed-{value}",
    )

    credentials = GoogleWorkspaceAdapter(
        None, value.db, token_id=token.id,
    ).credentials()
    assert credentials.token == "new-access"
    assert refresh_calls == ["safe-request"]
    assert value.db.get(GoogleOAuthToken, token.id).access_token == "sealed-new-access"


def test_generation_rotation_during_backoff_denies_stale_ingress(
        db_session, user_factory, monkeypatch):
    value = _pilot_world(db_session, user_factory)
    item = _message("rotated-generation-message", "history-rotation")
    provider = FlakyPagedGmail(
        {item["id"]: item},
        {None: {"messages": [{"id": item["id"]}]}},
        [SyntheticHttpError(429)],
    )
    draft_calls: list[int] = []
    _install_offline_runtime(monkeypatch, value, provider, draft_calls)

    def rotate(_delay):
        MailboxIdentityService().bind_verified_google_subject(
            value.db,
            organization_id=value.org.id,
            google_token_id=value.token.id,
            subject=value.identity.account_key,
        )
        value.db.commit()

    monkeypatch.setattr("app.integrations.google_retry.time.sleep", rotate)
    with pytest.raises(GoogleReadError, match="mailbox_generation_changed"):
        gmail.sync_gmail_project(
            value.project.id, value.db, value.user,
            query="synthetic-generation-rotation", max_results=1,
        )
    assert len(provider.list_calls) == 1
    assert value.db.scalar(select(Message).where(
        Message.provider_message_id == item["id"],
    )) is None
    assert not draft_calls


def test_attachment_download_retries_read_only_rate_limit_once(monkeypatch):
    calls = []
    delays = []

    class AttachmentService:
        def users(self): return self
        def messages(self): return self
        def attachments(self): return self

        def get(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(execute=lambda: (_ for _ in ()).throw(
                    SyntheticHttpError(429)
                ))
            return SimpleNamespace(execute=lambda: {"data": "dGVzdA==", "size": 4})

    monkeypatch.setattr("app.integrations.google_retry.time.sleep", delays.append)
    adapter = GmailProviderDownloadAdapter(
        AttachmentService(), provider_message_id="opaque-message",
        provider_attachment_id="opaque-attachment", expected_size=4, max_bytes=100,
    )
    opened = adapter.open()
    assert opened.stream.read() == b"test"
    assert len(calls) == 2 and delays == [pytest.approx(0.25)]


def test_attachment_token_rejection_is_content_free_and_not_retried(monkeypatch):
    calls = []

    class AttachmentService:
        def users(self): return self
        def messages(self): return self
        def attachments(self): return self

        def get(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(execute=lambda: (_ for _ in ()).throw(
                SyntheticHttpError(401)
            ))

    monkeypatch.setattr(
        "app.integrations.google_retry.time.sleep",
        lambda _delay: pytest.fail("authentication rejection must not back off"),
    )
    adapter = GmailProviderDownloadAdapter(
        AttachmentService(), provider_message_id="opaque-message",
        provider_attachment_id="opaque-attachment", expected_size=4, max_bytes=100,
    )
    with pytest.raises(GmailAttachmentDenied, match="provider_attachment_denied"):
        adapter.open()
    assert len(calls) == 1


def test_attachment_rotation_during_backoff_prevents_second_provider_read(monkeypatch):
    calls = []
    current = {"generation": 1}

    class AttachmentService:
        def users(self): return self
        def messages(self): return self
        def attachments(self): return self

        def get(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(execute=lambda: (_ for _ in ()).throw(
                SyntheticHttpError(429)
            ))

    def authorize():
        if current["generation"] != 1:
            raise GoogleReadError("mailbox_generation_changed", retryable=False)

    monkeypatch.setattr(
        "app.integrations.google_retry.time.sleep",
        lambda _delay: current.update(generation=2),
    )
    adapter = GmailProviderDownloadAdapter(
        AttachmentService(), provider_message_id="opaque-message",
        provider_attachment_id="opaque-attachment", expected_size=4, max_bytes=100,
    )
    with pytest.raises(GmailAttachmentDenied, match="provider_attachment_denied"):
        adapter.open(before_attempt=authorize)
    assert len(calls) == 1
