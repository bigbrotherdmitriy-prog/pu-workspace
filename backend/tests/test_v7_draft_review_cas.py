"""Synthetic exact-review regressions; no provider calls or credentials."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from app.api.responses import DraftUpdate, list_drafts, update_draft
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from test_response_drafts_api import _draft_world


def reviewed(db, user, draft):
    rows = list_drafts(draft.project_id, db, user)["drafts"]
    return next(row for row in rows if row["id"] == draft.id)


def token(db, user, draft):
    # Fallback only lets the pre-fix API reach its unsafe mutation; assertions
    # below independently require the actual read model token.
    return reviewed(db, user, draft).get("review_token", "a" * 64)


def test_read_model_exposes_exact_review_token(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    value = reviewed(db_session, user, draft).get("review_token")
    assert isinstance(value, str) and len(value) == 64
    assert set(value) <= set("0123456789abcdef")


def test_open_a_edit_b_then_approve_a_never_approves_b(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    original = token(db_session, user, draft)
    update_draft(draft.id, DraftUpdate(body="Human edit B", expected_review_token=original), db_session, user)
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(status="approved", expected_review_token=original), db_session, user)
    assert caught.value.status_code == 409
    db_session.refresh(draft)
    assert draft.body == "Human edit B" and draft.status == "draft"


def test_legacy_approval_without_review_is_denied(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(status="approved"), db_session, user)
    assert caught.value.status_code == 409


def test_changed_content_cannot_be_saved_and_approved_together(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(status="approved", body="Unreviewed replacement",
            expected_review_token=token(db_session, user, draft)), db_session, user)
    assert caught.value.status_code == 409
    assert draft.body == "Approved body"


def test_save_then_fresh_review_then_approve_echo(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    first = token(db_session, user, draft)
    saved = update_draft(draft.id, DraftUpdate(body="Saved draft B", expected_review_token=first), db_session, user)
    assert saved["status"] == "draft" and saved["review_token"] != first
    approved = update_draft(draft.id, DraftUpdate(status="approved", body="Saved draft B",
        expected_review_token=saved["review_token"]), db_session, user)
    assert approved["status"] == "approved"
    assert approved["review_token"] != saved["review_token"]
    details = " ".join(db_session.scalars(select(AuditLog.details)).all())
    assert "Saved draft B" not in details and first not in details


@pytest.mark.parametrize("field,value", [("body", "Changed body"), ("subject", "Changed subject"),
                                        ("recipient_to", "other@example.test")])
def test_every_content_change_revokes_approval(db_session, user_factory, field, value):
    user, draft = _draft_world(db_session, user_factory)
    result = update_draft(draft.id, DraftUpdate(expected_review_token=token(db_session, user, draft),
        **{field: value}), db_session, user)
    assert result["status"] == "draft" and result[field] == value


@pytest.mark.parametrize("payload", [{"body": "Legacy edit"}, {"status": "rejected"}, {"status": "draft"}])
def test_all_legacy_mutations_require_review(db_session, user_factory, payload):
    user, draft = _draft_world(db_session, user_factory)
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(**payload), db_session, user)
    assert caught.value.status_code == 409 and caught.value.detail == "draft_review_required"


@pytest.mark.parametrize("value", ["", "a" * 63, "G" * 64, "https://example.test/token"])
def test_malformed_review_tokens_are_rejected(value):
    with pytest.raises(ValidationError):
        DraftUpdate(expected_review_token=value)


def test_stale_identity_map_is_refreshed_before_compare(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    previous = token(db_session, user, draft)
    with Session(db_session.get_bind()) as other:
        other.execute(update(ResponseDraft).where(ResponseDraft.id == draft.id)
                      .values(body="Committed by another session", status="draft"))
        other.commit()
    assert draft.body == "Approved body"  # genuinely stale cached ORM object
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(status="approved", expected_review_token=previous), db_session, user)
    assert caught.value.detail == "draft_review_changed"
    assert draft.body == "Committed by another session" and draft.status == "draft"
    assert reviewed(db_session, user, draft)["body"] == "Committed by another session"


def test_pending_edit_is_not_flushed_or_discarded(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    previous = token(db_session, user, draft)
    draft.body = "Pending user change"
    writes = []
    def before_flush(*_):
        writes.append(True)
    event.listen(db_session, "before_flush", before_flush)
    try:
        with pytest.raises(HTTPException) as caught:
            update_draft(draft.id, DraftUpdate(status="approved", expected_review_token=previous), db_session, user)
        assert caught.value.detail == "draft_review_pending_changes"
        with pytest.raises(HTTPException):
            list_drafts(draft.project_id, db_session, user)
        assert draft.body == "Pending user change" and writes == []
    finally:
        event.remove(db_session, "before_flush", before_flush)


def test_invalid_payload_does_not_leave_partial_edit(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    previous = token(db_session, user, draft)
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(subject="Should not persist", body="   ",
            expected_review_token=previous), db_session, user)
    assert caught.value.status_code == 422
    assert draft.subject == "Approved subject" and draft not in db_session.dirty


def _source(db, user, draft):
    project = db.get(Project, draft.project_id)
    source = Message(organization_id=project.organization_id, project_id=project.id,
        created_by_user_id=user.id, source_type="email", source_external_id="synthetic-review-origin",
        source_name="Synthetic source", source_sender="source@example.test", source_thread_id="thread-A",
        content="Synthetic source body", summary="Summary", context_evidence="Synthetic evidence",
        context_confirmed=True)
    db.add(source); db.flush()
    draft.message_id = source.id
    db.commit()
    return source


@pytest.mark.parametrize("field,value", [("context_version", 2), ("origin_version", 2),
    ("source_thread_id", "thread-B"), ("source_sender", "other@example.test"),
    ("content", "Changed synthetic content"), ("context_confirmed", False)])
def test_origin_change_invalidates_review(db_session, user_factory, field, value):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    source = _source(db_session, user, draft)
    previous = token(db_session, user, draft)
    setattr(source, field, value); db_session.commit()
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(status="approved", expected_review_token=previous), db_session, user)
    assert caught.value.detail == "draft_review_changed"


def test_current_membership_is_required_despite_valid_token(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    previous = token(db_session, user, draft)
    db_session.execute(update(ProjectMember).where(ProjectMember.project_id == draft.project_id,
        ProjectMember.user_id == user.id).values(role="editor")); db_session.commit()
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(status="approved", expected_review_token=previous), db_session, user)
    assert caught.value.status_code == 403


@pytest.mark.parametrize("status", ["sending", "sent", "unknown"])
def test_dispatched_draft_is_not_editable(db_session, user_factory, status):
    user, draft = _draft_world(db_session, user_factory, role="manager")
    draft.status = status; db_session.commit()
    with pytest.raises(HTTPException) as caught:
        update_draft(draft.id, DraftUpdate(body="Unsafe replacement",
            expected_review_token=token(db_session, user, draft)), db_session, user)
    assert caught.value.status_code == 409
