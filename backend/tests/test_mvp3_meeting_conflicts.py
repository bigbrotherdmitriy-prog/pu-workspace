from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.management import MeetingCreate, create_meeting, meetings
from app.models.management import Meeting
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_contact import ProjectContact
from app.models.project_member import ProjectMember


def _world(db, user_factory, *, second_member=True):
    organization = Organization(name="Meeting conflict tenant")
    other_organization = Organization(name="Other tenant")
    db.add_all([organization, other_organization]); db.flush()
    actor = user_factory(name="Dispatcher")
    participant = user_factory(name="Engineer")
    other_participant = user_factory(name="Architect")
    project = Project(name="Alpha", organization_id=organization.id)
    second_project = Project(name="Beta", organization_id=organization.id)
    foreign_project = Project(name="Foreign", organization_id=other_organization.id)
    db.add_all([project, second_project, foreign_project]); db.flush()
    rows = [
        ProjectMember(project_id=project.id, user_id=actor.id, role="manager"),
        ProjectMember(project_id=project.id, user_id=participant.id, role="member"),
        ProjectMember(project_id=project.id, user_id=other_participant.id, role="member"),
    ]
    if second_member:
        rows.extend([
            ProjectMember(project_id=second_project.id, user_id=actor.id, role="manager"),
            ProjectMember(project_id=second_project.id, user_id=participant.id, role="member"),
        ])
    db.add_all(rows); db.commit()
    return actor, participant, other_participant, project, second_project, foreign_project, organization


def _create(db, actor, project, participant, title, start, duration=60, **overrides):
    values = {
        "project_id": project.id,
        "title": title,
        "scheduled_at": datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
        "duration_minutes": duration,
        "participant_user_ids": [participant.id],
    }
    values.update(overrides)
    return create_meeting(MeetingCreate(**values), db, actor)


@pytest.mark.parametrize(
    ("first_start", "first_duration", "second_start", "second_duration"),
    [
        ("2026-09-20T10:00:00", 60, "2026-09-20T10:30:00", 60),
        ("2026-09-20T10:00:00", 180, "2026-09-20T11:00:00", 30),
        ("2026-09-20T10:00:00", 60, "2026-09-20T10:00:00", 60),
    ],
)
def test_overlapping_meetings_warn_but_both_are_created(
    db_session, user_factory, first_start, first_duration, second_start, second_duration,
):
    actor, participant, _, project, *_ = _world(db_session, user_factory)
    first = _create(db_session, actor, project, participant, "First", first_start, first_duration)
    second = _create(db_session, actor, project, participant, "Second", second_start, second_duration)

    assert first["has_conflicts"] is False
    assert second["has_conflicts"] is True
    assert second["conflict_count"] == 1
    assert second["conflicts"][0]["meeting_id"] == first["id"]
    assert second["conflicts"][0]["participants"] == [{
        "kind": "user", "id": participant.id,
        "name": participant.name, "email": participant.email,
    }]
    assert db_session.get(Meeting, first["id"]) is not None
    assert db_session.get(Meeting, second["id"]) is not None

    listed = meetings(project.id, db_session, actor)
    assert all(item["has_conflicts"] for item in listed["meetings"])


def test_adjacent_meetings_use_half_open_intervals(db_session, user_factory):
    actor, participant, _, project, *_ = _world(db_session, user_factory)
    _create(db_session, actor, project, participant, "First", "2026-09-20T10:00:00", 60)
    second = _create(db_session, actor, project, participant, "Adjacent", "2026-09-20T11:00:00", 30)
    assert second["has_conflicts"] is False


def test_same_time_with_different_participants_does_not_warn(db_session, user_factory):
    actor, participant, other, project, *_ = _world(db_session, user_factory)
    _create(db_session, actor, project, participant, "First", "2026-09-20T10:00:00")
    second = _create(db_session, actor, project, other, "Other person", "2026-09-20T10:00:00")
    assert second["has_conflicts"] is False


def test_cancelled_meeting_does_not_conflict(db_session, user_factory):
    actor, participant, _, project, *_ = _world(db_session, user_factory)
    first = _create(db_session, actor, project, participant, "Cancelled", "2026-09-20T10:00:00")
    db_session.get(Meeting, first["id"]).status = "cancelled"
    db_session.commit()
    second = _create(db_session, actor, project, participant, "Replacement", "2026-09-20T10:30:00")
    assert second["has_conflicts"] is False


def test_legacy_text_participants_remain_visible_but_are_not_guessed(db_session, user_factory):
    actor, participant, _, project, *_ = _world(db_session, user_factory)
    legacy = create_meeting(MeetingCreate(
        project_id=project.id, title="Legacy", scheduled_at=datetime(2026, 9, 20, 10, tzinfo=timezone.utc),
        participants=participant.name,
    ), db_session, actor)
    structured = _create(db_session, actor, project, participant, "Structured", "2026-09-20T10:30:00")
    assert legacy["duration_minutes"] == 60
    assert legacy["participants"] == participant.name
    assert legacy["participant_refs"] == []
    assert structured["has_conflicts"] is False


def test_active_project_contact_can_participate_in_conflict(db_session, user_factory):
    actor, _, _, project, _, _, organization = _world(db_session, user_factory)
    contact = ProjectContact(
        organization_id=organization.id, project_id=project.id, created_by_user_id=actor.id,
        name="Client", email="client@example.test", normalized_email="client@example.test",
        active=True, confirmed=True,
    )
    db_session.add(contact); db_session.commit()
    first = create_meeting(MeetingCreate(
        project_id=project.id, title="Client first", scheduled_at=datetime(2026, 9, 20, 10, tzinfo=timezone.utc),
        duration_minutes=60, participant_contact_ids=[contact.id],
    ), db_session, actor)
    second = create_meeting(MeetingCreate(
        project_id=project.id, title="Client second", scheduled_at=datetime(2026, 9, 20, 10, 30, tzinfo=timezone.utc),
        duration_minutes=30, participant_contact_ids=[contact.id],
    ), db_session, actor)
    assert first["has_conflicts"] is False
    assert second["conflicts"][0]["participants"][0]["kind"] == "contact"


def test_cross_project_conflict_is_redacted_without_access(db_session, user_factory):
    actor, participant, _, project, second_project, *_ = _world(db_session, user_factory, second_member=False)
    hidden = Meeting(
        project_id=second_project.id, created_by_user_id=participant.id, title="Confidential meeting",
        scheduled_at=datetime(2026, 9, 20, 10, tzinfo=timezone.utc), duration_minutes=60,
    )
    db_session.add(hidden); db_session.flush()
    from app.models.management import MeetingParticipant
    db_session.add(MeetingParticipant(meeting_id=hidden.id, user_id=participant.id)); db_session.commit()

    visible = _create(db_session, actor, project, participant, "Visible", "2026-09-20T10:30:00")
    assert visible["has_conflicts"] is True
    assert visible["conflicts"][0] == {
        "meeting_id": None, "project_id": None, "title": "Занято в другом проекте",
        "overlap_from": datetime(2026, 9, 20, 10, 30, tzinfo=timezone.utc),
        "overlap_to": datetime(2026, 9, 20, 11, tzinfo=timezone.utc),
        "participants": [{"kind": "user", "id": participant.id,
                          "name": participant.name, "email": participant.email}],
        "redacted": True,
    }


def test_participants_must_belong_to_the_target_project(db_session, user_factory):
    actor, _, _, project, _, foreign_project, _ = _world(db_session, user_factory)
    foreign_user = user_factory(name="Foreign user")
    db_session.add(ProjectMember(project_id=foreign_project.id, user_id=foreign_user.id, role="member"))
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        _create(db_session, actor, project, foreign_user, "Invalid tenant", "2026-09-20T10:00:00")
    assert error.value.status_code == 422
    assert "состоять в проекте" in error.value.detail


def test_duration_contract_is_backward_compatible_and_bounded():
    unscheduled = MeetingCreate(project_id=1, title="Unscheduled", participants="legacy text")
    assert unscheduled.duration_minutes is None
    scheduled = MeetingCreate(
        project_id=1, title="Default duration", scheduled_at=datetime(2026, 9, 20, 10, tzinfo=timezone.utc),
    )
    assert scheduled.duration_minutes == 60
    with pytest.raises(ValueError):
        MeetingCreate(project_id=1, title="Invalid", duration_minutes=30)
    with pytest.raises(ValueError):
        MeetingCreate(
            project_id=1, title="Duplicate", scheduled_at=datetime(2026, 9, 20, 10, tzinfo=timezone.utc),
            participant_user_ids=[7, 7],
        )
