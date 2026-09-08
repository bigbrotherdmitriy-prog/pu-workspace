"""Exact human Send view, with a synthetic queue and no provider execution."""
import inspect
import pytest
from fastapi import HTTPException
from fastapi.openapi.utils import get_openapi
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api import gmail
from app.api.responses import list_drafts
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.provider_actions.contracts import ProviderActionError
from test_response_drafts_api import _draft_world
from test_v7_draft_review_cas import _source


def _token(db, user, draft):
    return next(row["review_token"] for row in list_drafts(draft.project_id, db, user)["drafts"]
                if row["id"] == draft.id)


def _send(db, user, draft, expected=None):
    # Reach the historical unsafe endpoint during RED, instead of failing only
    # on an unknown Python parameter. The assertions require zero queued work.
    if "payload" not in inspect.signature(gmail.send_gmail).parameters:
        return gmail.send_gmail(draft.id, db=db, user=user)
    payload = None if expected is None else gmail.DraftSend(expected_review_token=expected)
    return gmail.send_gmail(draft.id, db=db, user=user, payload=payload)


@pytest.fixture
def queued(monkeypatch):
    calls = []
    def enqueue(db, **kwargs):
        calls.append(kwargs)
        return {"action_id": "synthetic-send", "revision": 1, "job_id": 1}
    monkeypatch.setattr(gmail, "queue_confirmed_action", enqueue)
    monkeypatch.setattr(gmail, "google_workspace_for_project", lambda *_: pytest.fail("provider I/O forbidden"))
    monkeypatch.setattr(gmail, "google_workspace_for_mailbox", lambda *_: pytest.fail("provider I/O forbidden"))
    return calls


def test_stale_view_a_cannot_send_approved_b(db_session, user_factory, queued):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    old = _token(db_session, user, draft)
    draft.body = "Approved B by another reviewer"; db_session.commit()
    with pytest.raises(HTTPException) as caught:
        _send(db_session, user, draft, old)
    assert caught.value.status_code == 409
    assert queued == [] and draft.status == "approved"


def test_missing_send_token_does_not_queue_legacy_approved(db_session, user_factory, queued):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    with pytest.raises(HTTPException) as caught:
        _send(db_session, user, draft)
    assert caught.value.status_code == 409
    assert queued == [] and draft.status == "approved"


def test_fresh_explicit_send_queues_once_and_old_token_cannot_repeat(db_session, user_factory, queued):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    current = _token(db_session, user, draft)
    result = _send(db_session, user, draft, current)
    assert result["status"] == "queued" and draft.status == "sending"
    assert len(queued) == 1 and queued[0]["action_kind"] == "gmail.message.send"
    with pytest.raises(HTTPException) as caught:
        _send(db_session, user, draft, current)
    assert caught.value.status_code == 409 and len(queued) == 1


def test_editor_cannot_send_even_with_current_token(db_session, user_factory, queued):
    user, draft = _draft_world(db_session, user_factory, role="editor")
    with pytest.raises(HTTPException) as caught:
        _send(db_session, user, draft, _token(db_session, user, draft))
    assert caught.value.status_code == 403 and queued == []


def test_request_body_contract_is_exposed():
    schema = get_openapi(title="Synthetic send", version="test", routes=gmail.router.routes)
    request = schema["paths"]["/response-drafts/{draft_id}/send-gmail"]["post"]["requestBody"]
    assert "DraftSend" in str(request["content"]["application/json"]["schema"])
    assert "expected_review_token" in schema["components"]["schemas"]["DraftSend"]["properties"]


def test_stale_cached_draft_cannot_bypass_send_compare(db_session, user_factory, queued):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    prior = _token(db_session, user, draft)
    with Session(db_session.get_bind()) as other:
        other.execute(update(ResponseDraft).where(ResponseDraft.id == draft.id).values(body="New persisted content"))
        other.commit()
    assert draft.body == "Approved body"
    with pytest.raises(HTTPException) as caught:
        _send(db_session, user, draft, prior)
    assert caught.value.detail == "draft_review_changed" and queued == []


def test_new_sender_context_rejects_old_send_view(db_session, user_factory, queued):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    source = _source(db_session, user, draft)
    draft.recipient_to = None; db_session.commit()
    prior = _token(db_session, user, draft)
    source.source_sender = "new-target@example.test"; db_session.commit()
    with pytest.raises(HTTPException) as caught:
        _send(db_session, user, draft, prior)
    assert caught.value.status_code == 409 and queued == []


def test_revoked_manager_cannot_send_using_previously_read_token(db_session, user_factory, queued):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    prior = _token(db_session, user, draft)
    db_session.execute(update(ProjectMember).where(ProjectMember.project_id == draft.project_id,
        ProjectMember.user_id == user.id).values(role="viewer")); db_session.commit()
    with pytest.raises(HTTPException) as caught:
        _send(db_session, user, draft, prior)
    assert caught.value.status_code == 403 and queued == []


def test_already_sent_requires_current_view_and_current_rights(db_session, user_factory, queued):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    old = _token(db_session, user, draft)
    draft.status = "sent"; draft.sent_external_id = "synthetic-private-provider-id"; db_session.commit()
    with pytest.raises(HTTPException):
        _send(db_session, user, draft, old)
    current = _token(db_session, user, draft)
    outsider = user_factory(); db_session.commit()
    with pytest.raises(HTTPException) as caught:
        _send(db_session, outsider, draft, current)
    assert caught.value.status_code == 403
    assert "synthetic-private-provider-id" not in str(caught.value.detail)
    result = _send(db_session, user, draft, current)
    assert result["already_sent"] is True and result["gmail_message_id"] == draft.sent_external_id
    assert queued == []


def test_mailbox_denial_blocks_already_sent_disclosure(db_session, user_factory, queued, monkeypatch):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    _source(db_session, user, draft)
    draft.status = "sent"; draft.sent_external_id = "synthetic-private-provider-id"; db_session.commit()
    current = _token(db_session, user, draft)
    def denied(*args, **kwargs):
        raise ValueError("synthetic private mailbox denial")
    monkeypatch.setattr(gmail, "runtime_for_message", denied)
    with pytest.raises(HTTPException) as caught:
        _send(db_session, user, draft, current)
    assert caught.value.status_code == 409 and queued == []
    assert "synthetic private" not in str(caught.value.detail)


@pytest.mark.parametrize("failure", [ProviderActionError("payload_stale"), RuntimeError("synthetic unavailable")])
def test_queue_failure_rolls_back_sending_transition(db_session, user_factory, monkeypatch, failure):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    prior = _token(db_session, user, draft)
    def fail(db, **kwargs):
        db.flush()
        assert draft.status == "sending"
        raise failure
    monkeypatch.setattr(gmail, "queue_confirmed_action", fail)
    with pytest.raises((HTTPException, RuntimeError)):
        _send(db_session, user, draft, prior)
    db_session.refresh(draft)
    assert draft.status == "approved"
    assert _token(db_session, user, draft) == prior


def test_sending_state_is_in_queue_commit_not_a_later_transaction(db_session, user_factory, monkeypatch):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    prior = _token(db_session, user, draft)
    def committed_queue(db, **kwargs):
        assert draft.status == "sending"
        db.commit()
        with Session(db.get_bind()) as observer:
            assert observer.get(ResponseDraft, draft.id).status == "sending"
        return {"action_id": "synthetic", "revision": 1, "job_id": 1}
    monkeypatch.setattr(gmail, "queue_confirmed_action", committed_queue)
    assert _send(db_session, user, draft, prior)["status"] == "queued"
