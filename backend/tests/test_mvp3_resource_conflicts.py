from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.management import (
    BookableResourceCreate,
    BookableResourceUpdate,
    MeetingCreate,
    bookable_resources,
    create_bookable_resource,
    create_meeting,
    update_bookable_resource,
)
from app.models.management import BookableResource, Meeting, MeetingResource
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db, user_factory, *, actor_in_second=True):
    organization = Organization(name="Resource tenant")
    other_organization = Organization(name="Other resource tenant")
    db.add_all([organization, other_organization]); db.flush()
    actor = user_factory(name="Resource manager")
    participant = user_factory(name="Resource participant")
    outsider = user_factory(name="Resource outsider")
    project = Project(name="Alpha", organization_id=organization.id)
    second_project = Project(name="Beta", organization_id=organization.id)
    foreign_project = Project(name="Foreign", organization_id=other_organization.id)
    db.add_all([project, second_project, foreign_project]); db.flush()
    memberships = [
        ProjectMember(project_id=project.id, user_id=actor.id, role="manager"),
        ProjectMember(project_id=project.id, user_id=participant.id, role="member"),
        ProjectMember(project_id=foreign_project.id, user_id=outsider.id, role="manager"),
    ]
    if actor_in_second:
        memberships.append(ProjectMember(project_id=second_project.id, user_id=actor.id, role="manager"))
    db.add_all(memberships); db.commit()
    return actor, participant, outsider, project, second_project, foreign_project, organization


def _resource(db, actor, project, *, name="Переговорная 1", capacity=8):
    return create_bookable_resource(BookableResourceCreate(
        project_id=project.id, kind="room", name=name,
        timezone="Europe/Moscow", capacity=capacity,
    ), db, actor)


def _meeting(db, actor, project, resource_id, title, start, *, participant_ids=None, duration=60):
    return create_meeting(MeetingCreate(
        project_id=project.id,
        title=title,
        scheduled_at=datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
        duration_minutes=duration,
        participant_user_ids=participant_ids or [],
        resource_ids=[resource_id],
    ), db, actor)


def test_overlapping_resource_bookings_warn_but_both_are_created(db_session, user_factory):
    actor, participant, _, project, *_ = _world(db_session, user_factory)
    resource = _resource(db_session, actor, project, capacity=1)
    first = _meeting(db_session, actor, project, resource["id"], "First", "2026-09-26T10:00:00",
                     participant_ids=[participant.id])
    second = _meeting(db_session, actor, project, resource["id"], "Second", "2026-09-26T10:30:00",
                      participant_ids=[participant.id])

    assert first["has_conflicts"] is False
    assert second["has_conflicts"] is True
    assert second["conflict_count"] == 1
    assert second["conflicts"][0]["resources"] == [{
        "id": resource["id"], "kind": "room", "name": "Переговорная 1",
        "timezone": "Europe/Moscow", "capacity": 1, "active": True,
    }]
    assert second["conflicts"][0]["participants"][0]["id"] == participant.id
    assert db_session.query(Meeting).count() == 2


def test_resource_conflict_rules_cover_adjacent_different_and_cancelled(db_session, user_factory):
    actor, _, _, project, *_ = _world(db_session, user_factory)
    first_resource = _resource(db_session, actor, project, name="Комната A")
    second_resource = _resource(db_session, actor, project, name="Комната B")
    first = _meeting(db_session, actor, project, first_resource["id"], "First", "2026-09-26T10:00:00")
    adjacent = _meeting(db_session, actor, project, first_resource["id"], "Adjacent", "2026-09-26T11:00:00")
    different = _meeting(db_session, actor, project, second_resource["id"], "Different", "2026-09-26T10:30:00")
    db_session.get(Meeting, first["id"]).status = "cancelled"; db_session.commit()
    replacement = _meeting(
        db_session, actor, project, first_resource["id"], "Replacement",
        "2026-09-26T10:00:00", duration=30,
    )

    assert adjacent["has_conflicts"] is False
    assert different["has_conflicts"] is False
    assert replacement["has_conflicts"] is False


def test_capacity_is_a_warning_not_a_block(db_session, user_factory):
    actor, participant, _, project, *_ = _world(db_session, user_factory)
    second = user_factory(name="Second participant")
    db_session.add(ProjectMember(project_id=project.id, user_id=second.id, role="member")); db_session.commit()
    resource = _resource(db_session, actor, project, capacity=1)

    created = _meeting(
        db_session, actor, project, resource["id"], "Crowded", "2026-09-26T10:00:00",
        participant_ids=[participant.id, second.id],
    )

    assert created["has_resource_warnings"] is True
    assert created["resource_warnings"] == [{
        "code": "capacity_exceeded", "resource_id": resource["id"],
        "resource_name": "Переговорная 1", "capacity": 1, "participant_count": 2,
    }]
    assert db_session.get(Meeting, created["id"]) is not None


def test_resource_catalog_is_org_scoped_versioned_and_soft_deactivated(db_session, user_factory):
    actor, _, _, project, second_project, *_ = _world(db_session, user_factory)
    resource = _resource(db_session, actor, project)

    listed = bookable_resources(second_project.id, db_session, actor)
    assert [row["id"] for row in listed["resources"]] == [resource["id"]]
    updated = update_bookable_resource(resource["id"], BookableResourceUpdate(
        expected_record_version=1, active=False,
    ), db_session, actor)
    assert updated["record_version"] == 2
    assert updated["active"] is False
    assert bookable_resources(project.id, db_session, actor)["resources"] == []
    assert bookable_resources(project.id, db_session, actor, include_inactive=True)["resources"][0]["active"] is False
    with pytest.raises(HTTPException) as error:
        _meeting(db_session, actor, project, resource["id"], "Inactive", "2026-09-26T10:00:00")
    assert error.value.status_code == 422


def test_cross_tenant_resource_is_rejected_without_disclosing_details(db_session, user_factory):
    actor, _, outsider, project, _, foreign_project, *_ = _world(db_session, user_factory)
    foreign = _resource(db_session, outsider, foreign_project, name="Foreign room")
    with pytest.raises(HTTPException) as error:
        _meeting(db_session, actor, project, foreign["id"], "Invalid", "2026-09-26T10:00:00")
    assert error.value.status_code == 422
    assert error.value.detail == "Ресурс должен быть активным и относиться к организации проекта"


def test_cross_project_resource_conflict_redacts_hidden_meeting(db_session, user_factory):
    actor, participant, _, project, second_project, *_ = _world(db_session, user_factory, actor_in_second=False)
    resource = _resource(db_session, actor, project)
    hidden = Meeting(
        project_id=second_project.id, created_by_user_id=participant.id,
        title="Confidential room booking",
        scheduled_at=datetime(2026, 9, 26, 10, tzinfo=timezone.utc), duration_minutes=60,
    )
    db_session.add(hidden); db_session.flush()
    db_session.add(MeetingResource(meeting_id=hidden.id, resource_id=resource["id"])); db_session.commit()

    visible = _meeting(db_session, actor, project, resource["id"], "Visible", "2026-09-26T10:30:00")
    conflict = visible["conflicts"][0]
    assert conflict["meeting_id"] is None and conflict["project_id"] is None
    assert conflict["title"] == "Занято в другом проекте"
    assert conflict["resources"][0]["id"] == resource["id"]
    assert conflict["redacted"] is True


def test_resource_contract_is_backward_compatible_and_rejects_duplicates():
    legacy = MeetingCreate(project_id=1, title="Legacy")
    assert legacy.resource_ids == []
    with pytest.raises(ValueError):
        MeetingCreate(project_id=1, title="Duplicate resources", resource_ids=[3, 3])
    with pytest.raises(ValueError):
        BookableResourceCreate(project_id=1, kind="room", name="Room", timezone="Invalid/Timezone")
    with pytest.raises(ValueError):
        BookableResourceUpdate(expected_record_version=1, active=None)


def test_legacy_participant_text_is_never_guessed_as_resource(db_session, user_factory):
    actor, _, _, project, *_ = _world(db_session, user_factory)
    _resource(db_session, actor, project, name="Переговорная Север")
    first = create_meeting(MeetingCreate(
        project_id=project.id, title="Legacy text",
        scheduled_at=datetime(2026, 9, 26, 10, tzinfo=timezone.utc),
        participants="Переговорная Север",
    ), db_session, actor)
    second = create_meeting(MeetingCreate(
        project_id=project.id, title="Another legacy text",
        scheduled_at=datetime(2026, 9, 26, 10, 30, tzinfo=timezone.utc),
        participants="Переговорная Север",
    ), db_session, actor)
    assert first["resource_refs"] == []
    assert second["has_conflicts"] is False
