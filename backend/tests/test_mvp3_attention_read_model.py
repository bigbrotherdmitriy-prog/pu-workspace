from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.management import _meeting_payload, attention_feed
from app.models.management import (
    Meeting, MeetingParticipant, MeetingProposal, MeetingSourceBinding, Notification, Obligation,
)
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_contact import ContactConflict, ProjectContact
from app.models.project_member import ProjectMember


def _id() -> str:
    return str(uuid4())


def _world(db, user_factory):
    organization = Organization(name="Attention tenant")
    other_organization = Organization(name="Hidden tenant")
    db.add_all([organization, other_organization]); db.flush()
    viewer = user_factory(name="Attention viewer")
    hidden_owner = user_factory(name="Hidden owner")
    project = Project(name="Visible", organization_id=organization.id)
    hidden = Project(name="Hidden", organization_id=organization.id)
    foreign = Project(name="Foreign", organization_id=other_organization.id)
    db.add_all([project, hidden, foreign]); db.flush()
    db.add_all([
        ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"),
        ProjectMember(project_id=hidden.id, user_id=hidden_owner.id, role="owner"),
        ProjectMember(project_id=foreign.id, user_id=hidden_owner.id, role="owner"),
    ])
    contract = Contract(project_id=project.id, number="ATT-1", title="Attention contract")
    db.add(contract); db.flush()
    return viewer, hidden_owner, project, hidden, foreign, contract


def _obligation(db, project, owner, sequence, *, contract_id=None, status="needs_confirmation"):
    item = Obligation(
        project_id=project.id, contract_id=contract_id, owner_user_id=owner.id,
        title=f"Obligation {sequence}", status=status,
        due_date=date(2026, 9, 20 + sequence), source_type="message",
        source_id=f"message:{sequence}", source_name=f"mail-{sequence}.eml",
        source_excerpt=f"Evidence {sequence}", source_hash=f"{sequence:064x}", confidence=0.9,
    )
    db.add(item); db.flush()
    return item


def test_attention_feed_has_exact_origin_pending_approvals_and_redacted_conflicts(db_session, user_factory):
    viewer, hidden_owner, project, hidden, foreign, contract = _world(db_session, user_factory)
    obligation = _obligation(db_session, project, viewer, 1, contract_id=contract.id)
    _obligation(db_session, foreign, hidden_owner, 2)
    db_session.add(Notification(
        project_id=project.id, user_id=viewer.id, kind="deadline", title="Deadline",
        body="Due soon", entity_type="obligation", entity_id=obligation.id,
        dedupe_key="deadline:1", is_read=False,
    ))

    visible_meeting = Meeting(
        project_id=project.id, contract_id=contract.id, created_by_user_id=viewer.id,
        title="Visible meeting", scheduled_at=datetime(2026, 9, 25, 10, tzinfo=timezone.utc),
        duration_minutes=60, status="planned",
    )
    hidden_meeting = Meeting(
        project_id=hidden.id, created_by_user_id=hidden_owner.id,
        title="Secret acquisition", scheduled_at=datetime(2026, 9, 25, 10, 30, tzinfo=timezone.utc),
        duration_minutes=60, status="planned",
    )
    db_session.add_all([visible_meeting, hidden_meeting]); db_session.flush()
    db_session.add_all([
        MeetingParticipant(meeting_id=visible_meeting.id, user_id=viewer.id),
        MeetingParticipant(meeting_id=hidden_meeting.id, user_id=viewer.id),
    ])
    binding = MeetingSourceBinding(
        id=_id(), organization_id=project.organization_id, project_id=project.id,
        meeting_id=visible_meeting.id, meeting_record_version=2,
        source_id=_id(), source_version_id=_id(), evidence_id=_id(), materialization_id=_id(),
        command_id=_id(), command_hash="a" * 64, bound_by_user_id=viewer.id,
    )
    db_session.add(binding); db_session.flush()
    proposal = MeetingProposal(
        organization_id=project.organization_id, project_id=project.id,
        meeting_id=visible_meeting.id, binding_id=binding.id, proposal_type="task",
        payload={"title": "Approve the minutes task"}, fingerprint="b" * 64,
        status="proposed", created_by_user_id=viewer.id,
    )
    contact = ProjectContact(
        organization_id=project.organization_id, project_id=project.id,
        contract_id=contract.id, created_by_user_id=viewer.id, name="Contractor",
        email="contractor@example.test", normalized_email="contractor@example.test",
    )
    db_session.add_all([proposal, contact]); db_session.flush()
    conflict = ContactConflict(
        organization_id=project.organization_id, contact_id=contact.id,
        current_project_id=project.id, candidate_project_id=hidden.id,
        normalized_email=contact.normalized_email, status="pending",
    )
    hidden_contact = ProjectContact(
        organization_id=project.organization_id, project_id=hidden.id,
        created_by_user_id=hidden_owner.id, name="Secret contact",
        email="secret@example.test", normalized_email="secret@example.test",
    )
    db_session.add_all([conflict, hidden_contact]); db_session.flush()
    db_session.add(ContactConflict(
        organization_id=project.organization_id, contact_id=hidden_contact.id,
        current_project_id=hidden.id, candidate_project_id=project.id,
        normalized_email=hidden_contact.normalized_email, status="pending",
    ))
    db_session.commit()

    result = attention_feed(project_id=project.id, limit=100, db=db_session, user=viewer)

    kinds = {item["kind"] for item in result["items"]}
    assert {"obligation", "notification", "meeting_proposal", "contact_conflict", "meeting_conflict"} <= kinds
    assert not any(item["project_id"] == foreign.id for item in result["items"])
    obligation_item = next(item for item in result["items"] if item["kind"] == "obligation")
    assert obligation_item["origin"] == {
        "type": "message", "id": "message:1", "name": "mail-1.eml",
        "excerpt": "Evidence 1", "hash": f"{1:064x}",
    }
    proposal_item = next(item for item in result["items"] if item["kind"] == "meeting_proposal")
    assert proposal_item["origin"]["source_version_id"] == binding.source_version_id
    assert proposal_item["origin"]["evidence_id"] == binding.evidence_id
    meeting_item = next(item for item in result["items"] if item["kind"] == "meeting_conflict")
    assert meeting_item["origin"]["redacted_conflict_count"] == 1
    assert "Secret acquisition" not in str(result)
    assert "Secret contact" not in str(result)
    assert any(item["origin"].get("redacted") for item in result["items"]
               if item["kind"] == "contact_conflict")
    assert all(item["navigation"]["project_id"] == project.id for item in result["items"])


def test_attention_cursor_is_stable_bounded_and_filters_are_server_side(db_session, user_factory):
    viewer, _, project, _, _, contract = _world(db_session, user_factory)
    rows = [_obligation(db_session, project, viewer, number, contract_id=contract.id) for number in range(1, 7)]
    db_session.commit()

    seen = []
    cursor = None
    while True:
        page = attention_feed(
            project_id=project.id, contract_id=contract.id, owner_user_id=viewer.id,
            kind="obligation", status="needs_confirmation", date_from=date(2026, 9, 21),
            date_to=date(2026, 9, 26), cursor=cursor, limit=2, db=db_session, user=viewer,
        )
        seen.extend(item["entity_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == [row.id for row in rows]
    assert len(seen) == len(set(seen)) == 6
    with pytest.raises(HTTPException, match="limit must be"):
        attention_feed(project_id=project.id, limit=201, db=db_session, user=viewer)
    with pytest.raises(HTTPException, match="Unsupported attention kind"):
        attention_feed(project_id=project.id, kind="secret", db=db_session, user=viewer)
    with pytest.raises(HTTPException, match="Invalid attention cursor"):
        attention_feed(project_id=project.id, cursor="not-a-cursor", db=db_session, user=viewer)


def test_attention_feed_fails_closed_for_unrelated_project(db_session, user_factory):
    viewer, _, _, hidden, _, _ = _world(db_session, user_factory)
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        attention_feed(project_id=hidden.id, db=db_session, user=viewer)
    assert error.value.status_code == 403
