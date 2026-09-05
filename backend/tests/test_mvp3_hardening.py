from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api import management as management_api
from app.api.governance import DecisionUpdate, RiskUpdate, update_decision, update_risk
from app.api.management import (
    MeetingUpdate,
    NotificationRead,
    ObligationUpdate,
    finish_meeting,
    read_notification,
    update_obligation,
)
from app.api.project_contacts import (
    ContactConflictResolve,
    ContactUpdate,
    discover_contact_from_message,
    resolve_contact_conflict,
    update_contact,
)
from app.api.tasks import TaskUpdate, update_task
from app.models.governance import Decision, Risk
from app.models.management import ManagementHistory, Meeting, Notification, Obligation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_contact import ContactConflict, ProjectContact
from app.models.project_member import ProjectMember
from app.models.task import Task


def world(db, user_factory, *, both_projects=True):
    organization = Organization(name="MVP3 tenant")
    db.add(organization); db.flush()
    user = user_factory()
    first = Project(name="First", organization_id=organization.id)
    second = Project(name="Second", organization_id=organization.id)
    db.add_all([first, second]); db.flush()
    db.add(ProjectMember(project_id=first.id, user_id=user.id, role="manager"))
    if both_projects:
        db.add(ProjectMember(project_id=second.id, user_id=user.id, role="manager"))
    db.flush()
    return organization, user, first, second


def sources(owner_id, project_id):
    task = Task(project_id=project_id, assignee_user_id=owner_id, created_by_user_id=owner_id,
                title="Task", status="assigned", priority="normal", source_type="manual",
                source_file_id="source-1", source_file_name="source.txt", source_excerpt="evidence",
                source_excerpt_hash="1" * 64, confidence=1.0)
    obligation = Obligation(project_id=project_id, owner_user_id=owner_id, title="Obligation",
                            source_type="manual", source_id="source-2", source_name="source.txt",
                            source_excerpt="evidence", source_hash="2" * 64, confidence=1.0)
    meeting = Meeting(project_id=project_id, created_by_user_id=owner_id, title="Meeting")
    risk = Risk(project_id=project_id, owner_user_id=owner_id, title="Risk", description="Risk description",
                source_type="manual", source_id="source-3", source_name="source.txt",
                source_excerpt="evidence", source_hash="3" * 64, confidence=1.0)
    decision = Decision(project_id=project_id, initiator_user_id=owner_id, question="Decision?",
                        source_type="manual", source_id="source-4", source_name="source.txt",
                        source_excerpt="evidence", source_hash="4" * 64, confidence=1.0)
    return task, obligation, meeting, risk, decision


@pytest.mark.parametrize("kind", ["task", "obligation", "meeting", "risk", "decision"])
def test_every_management_mutation_rejects_stale_version_and_appends_history(db_session, user_factory, kind):
    _, user, project, _ = world(db_session, user_factory)
    task, obligation, meeting, risk, decision = sources(user.id, project.id)
    db_session.add_all([task, obligation, meeting, risk, decision]); db_session.commit()
    cases = {
        "task": (task, update_task, TaskUpdate(status="in_progress", expected_record_version=1)),
        "obligation": (obligation, update_obligation, ObligationUpdate(status="confirmed", expected_record_version=1)),
        "meeting": (meeting, finish_meeting, MeetingUpdate(minutes="Совещание отменено пользователем.", status="cancelled", expected_record_version=1)),
        "risk": (risk, update_risk, RiskUpdate(status="confirmed", expected_record_version=1)),
        "decision": (decision, update_decision, DecisionUpdate(status="confirmed", expected_record_version=1)),
    }
    item, handler, command = cases[kind]
    result = handler(item.id, command, db_session, user)
    assert result["record_version"] == 2
    with pytest.raises(HTTPException) as error:
        handler(item.id, command, db_session, user)
    assert error.value.status_code == 409
    rows = list(db_session.scalars(select(ManagementHistory).where(
        ManagementHistory.entity_type == kind, ManagementHistory.entity_id == item.id,
    )))
    assert len(rows) == 1
    assert rows[0].record_version == 2
    assert rows[0].old_values != rows[0].new_values
    assert rows[0].actor_user_id == user.id


def test_marking_notification_read_is_cas_versioned_and_audited(db_session, user_factory):
    _, user, project, _ = world(db_session, user_factory)
    item = Notification(
        project_id=project.id, user_id=user.id, kind="deadline", title="Срок",
        body="Проверьте обязательство", entity_type="obligation", entity_id=17,
        dedupe_key="notification-cas-test",
    )
    db_session.add(item); db_session.commit()

    result = read_notification(
        item.id, NotificationRead(expected_record_version=1), db_session, user,
    )
    assert result == {"id": item.id, "record_version": 2, "is_read": True}
    history = db_session.scalar(select(ManagementHistory).where(
        ManagementHistory.entity_type == "notification",
        ManagementHistory.entity_id == item.id,
    ))
    assert history.action == "read" and history.record_version == 2

    with pytest.raises(HTTPException) as error:
        read_notification(
            item.id, NotificationRead(expected_record_version=1), db_session, user,
        )
    assert error.value.status_code == 409


def test_project_contact_cas_and_history(db_session, user_factory):
    organization, user, project, _ = world(db_session, user_factory)
    contact = ProjectContact(organization_id=organization.id, project_id=project.id,
                             created_by_user_id=user.id, name="Client", email="client@example.test",
                             normalized_email="client@example.test", confirmed=False)
    db_session.add(contact); db_session.commit()
    result = update_contact(contact.id, ContactUpdate(confirmed=True, expected_record_version=1), db_session, user)
    assert result["record_version"] == 2
    with pytest.raises(HTTPException) as error:
        update_contact(contact.id, ContactUpdate(active=False, expected_record_version=1), db_session, user)
    assert error.value.status_code == 409
    assert db_session.scalar(select(func.count()).select_from(ManagementHistory).where(
        ManagementHistory.entity_type == "project_contact", ManagementHistory.entity_id == contact.id,
    )) == 1


def test_meeting_completion_and_derivation_rollback_together(db_session, user_factory, monkeypatch):
    _, user, project, _ = world(db_session, user_factory)
    meeting = Meeting(project_id=project.id, created_by_user_id=user.id, title="Atomic meeting")
    db_session.add(meeting); db_session.commit(); meeting_id = meeting.id

    def create_task_then_legacy_commit(db, project_id, *args, **kwargs):
        task = Task(project_id=project_id, assignee_user_id=user.id, created_by_user_id=user.id,
                    title="Would be rolled back", status="assigned", priority="normal", source_type="meeting",
                    source_file_id="atomic-source", source_file_name="meeting.txt", source_excerpt="evidence",
                    source_excerpt_hash="8" * 64, confidence=1.0)
        db.add(task); db.flush(); db.commit()
        return [task]

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic extraction failure")

    monkeypatch.setattr(management_api, "create_tasks_from_files", create_task_then_legacy_commit)
    monkeypatch.setattr(management_api, "create_governance_items", fail)
    with pytest.raises(RuntimeError, match="synthetic extraction failure"):
        finish_meeting(meeting_id, MeetingUpdate(minutes="Нужно выполнить обязательство до пятницы.",
                                                  status="completed", expected_record_version=1),
                       db_session, user)
    db_session.rollback()
    persisted = db_session.get(Meeting, meeting_id)
    assert persisted.status == "planned"
    assert persisted.record_version == 1
    assert db_session.scalar(select(func.count()).select_from(Task).where(Task.project_id == project.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(ManagementHistory).where(
        ManagementHistory.entity_type == "meeting", ManagementHistory.entity_id == meeting_id,
    )) == 0


def test_cross_project_contact_discovery_creates_resolvable_conflict(db_session, user_factory):
    organization, user, first, second = world(db_session, user_factory)
    contact = ProjectContact(organization_id=organization.id, project_id=first.id,
                             created_by_user_id=user.id, name="Client", email="client@example.test",
                             normalized_email="client@example.test", confirmed=True)
    db_session.add(contact); db_session.commit()
    assert discover_contact_from_message(db_session, second.id, "Client <CLIENT@example.test>", "New context", user) is None
    conflict = db_session.scalar(select(ContactConflict).where(ContactConflict.status == "pending"))
    assert conflict is not None
    result = resolve_contact_conflict(
        conflict.id,
        ContactConflictResolve(expected_record_version=1, expected_contact_record_version=1,
                               resolution="move_to_candidate", reason="Подтверждено владельцами проектов"),
        db_session, user,
    )
    assert result["status"] == "resolved"
    assert result["contact"]["project_id"] == second.id
    assert result["contact"]["record_version"] == 2


def test_contact_conflict_cannot_be_resolved_without_access_to_both_projects(db_session, user_factory):
    organization, user, first, second = world(db_session, user_factory, both_projects=False)
    contact = ProjectContact(organization_id=organization.id, project_id=first.id,
                             created_by_user_id=user.id, name="Client", email="client@example.test",
                             normalized_email="client@example.test", confirmed=True)
    db_session.add(contact); db_session.flush()
    conflict = ContactConflict(organization_id=organization.id, contact_id=contact.id,
                               current_project_id=first.id, candidate_project_id=second.id,
                               normalized_email=contact.normalized_email)
    db_session.add(conflict); db_session.commit()
    with pytest.raises(HTTPException) as error:
        resolve_contact_conflict(
            conflict.id,
            ContactConflictResolve(resolution="keep_current", reason="Проверка прав"),
            db_session, user,
        )
    assert error.value.status_code == 403


def test_contact_conflict_rejects_cross_tenant_candidate_binding(db_session, user_factory):
    organization, user, first, _ = world(db_session, user_factory)
    other_organization = Organization(name="Other tenant")
    db_session.add(other_organization); db_session.flush()
    foreign = Project(name="Foreign", organization_id=other_organization.id)
    db_session.add(foreign); db_session.flush()
    db_session.add(ProjectMember(project_id=foreign.id, user_id=user.id, role="manager"))
    contact = ProjectContact(organization_id=organization.id, project_id=first.id,
                             created_by_user_id=user.id, name="Client", email="client@example.test",
                             normalized_email="client@example.test", confirmed=True)
    db_session.add(contact); db_session.flush()
    conflict = ContactConflict(organization_id=organization.id, contact_id=contact.id,
                               current_project_id=first.id, candidate_project_id=foreign.id,
                               normalized_email=contact.normalized_email)
    db_session.add(conflict); db_session.commit()
    with pytest.raises(HTTPException) as error:
        resolve_contact_conflict(
            conflict.id,
            ContactConflictResolve(resolution="move_to_candidate", reason="Попытка чужой привязки"),
            db_session, user,
        )
    assert error.value.status_code == 409
