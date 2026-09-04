import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.responses import DraftUpdate, router, update_draft
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft


def test_response_draft_update_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/response-drafts/{draft_id}" in paths


def test_response_draft_status_is_restricted():
    with pytest.raises(ValidationError):
        DraftUpdate(status="sent")


def test_response_draft_accepts_reviewed_body():
    payload = DraftUpdate(status="approved", body="Подтверждённый текст ответа")
    assert payload.status == "approved"


def _draft_world(db_session, user_factory, *, role="editor"):
    user = user_factory()
    organization = Organization(name="Synthetic organization")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    draft = ResponseDraft(
        project_id=project.id,
        reviewer_user_id=user.id,
        subject="Approved subject",
        body="Approved body",
        recipient_to="recipient@example.test",
        status="approved",
        source_file_id="synthetic-message:1",
        source_file_name="Synthetic message",
        source_excerpt="Synthetic excerpt",
        source_excerpt_hash="a" * 64,
        confidence=0.9,
    )
    db_session.add(draft)
    db_session.commit()
    return user, draft


def test_editing_an_approved_draft_invalidates_previous_approval(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory)

    result = update_draft(
        draft.id,
        DraftUpdate(body="Changed after approval"),
        db_session,
        user,
    )

    assert result["status"] == "draft"
    assert draft.status == "draft"


def test_recipient_is_editable_but_change_requires_fresh_confirmation(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory)

    result = update_draft(
        draft.id,
        DraftUpdate(recipient_to="new-recipient@example.test"),
        db_session,
        user,
    )

    assert result["recipient_to"] == "new-recipient@example.test"
    assert result["status"] == "draft"


@pytest.mark.parametrize("recipient", ["invalid", "a@example.test,b@example.test", "Name <a@example.test>"])
def test_recipient_edit_rejects_ambiguous_or_invalid_addresses(db_session, user_factory, recipient):
    user, draft = _draft_world(db_session, user_factory)

    with pytest.raises(HTTPException) as error:
        update_draft(draft.id, DraftUpdate(recipient_to=recipient), db_session, user)

    assert error.value.status_code == 422
    assert draft.recipient_to == "recipient@example.test"


def test_editor_cannot_approve_external_email_envelope(db_session, user_factory):
    user, draft = _draft_world(db_session, user_factory, role="editor")
    draft.status = "draft"
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        update_draft(draft.id, DraftUpdate(status="approved"), db_session, user)

    assert error.value.status_code == 403
    assert draft.status == "draft"
