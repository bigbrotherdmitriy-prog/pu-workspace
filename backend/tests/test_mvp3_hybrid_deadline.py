from datetime import date

from sqlalchemy import select

from app.api.tasks import TaskUpdate, list_tasks, update_task
from app.models.management import Obligation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task, TaskDueDateHistory


def _world(db, user_factory, *, obligation_due_date=date(2026, 9, 26)):
    user = user_factory()
    organization = Organization(name="MVP3 hybrid deadline tenant")
    db.add(organization)
    db.flush()
    project = Project(name="MVP3 hybrid deadline project", organization_id=organization.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    task = Task(
        project_id=project.id,
        assignee_user_id=user.id,
        created_by_user_id=user.id,
        title="Подготовить техническое задание",
        status="assigned",
        priority="normal",
        due_date=obligation_due_date,
        source_type="email",
        source_file_id="mvp3-hybrid-deadline-source",
        source_file_name="source.txt",
        source_excerpt="Подготовить техническое задание до 26.09.2026",
        source_excerpt_hash="a" * 64,
        confidence=1.0,
    )
    db.add(task)
    db.flush()
    obligation = Obligation(
        project_id=project.id,
        owner_user_id=user.id,
        task_id=task.id,
        title=task.title,
        status="confirmed",
        due_date=obligation_due_date,
        source_type="email",
        source_id="mvp3-hybrid-deadline-source",
        source_name="source.txt",
        source_excerpt=task.source_excerpt,
        source_hash=task.source_excerpt_hash,
        confidence=1.0,
    )
    db.add(obligation)
    db.commit()
    return user, project, task, obligation


def test_task_due_date_changes_without_rewriting_original_obligation(db_session, user_factory):
    user, project, task, obligation = _world(db_session, user_factory)
    original_due_date = obligation.due_date

    before = list_tasks(project.id, db_session, user)["tasks"][0]
    assert before["original_obligation_id"] == obligation.id
    assert before["original_obligation_due_date"] == original_due_date
    assert before["due_date_adjusted"] is False

    working_due_date = date(2026, 10, 3)
    update_task(
        task.id,
        TaskUpdate(
            expected_record_version=1,
            due_date=working_due_date,
            due_change_reason="Срок согласован с руководителем проекта",
        ),
        db_session,
        user,
    )

    db_session.refresh(obligation)
    assert obligation.due_date == original_due_date
    after = list_tasks(project.id, db_session, user)["tasks"][0]
    assert after["due_date"] == working_due_date
    assert after["original_obligation_due_date"] == original_due_date
    assert after["due_date_adjusted"] is True
    change = db_session.scalar(select(TaskDueDateHistory).where(TaskDueDateHistory.task_id == task.id))
    assert change.old_due_date == original_due_date
    assert change.new_due_date == working_due_date
    assert change.reason == "Срок согласован с руководителем проекта"


def test_added_working_deadline_is_explicit_when_original_had_no_date(db_session, user_factory):
    user, project, task, obligation = _world(db_session, user_factory, obligation_due_date=None)

    update_task(
        task.id,
        TaskUpdate(
            expected_record_version=1,
            due_date=date(2026, 10, 3),
            due_change_reason="Рабочий срок назначен руководителем",
        ),
        db_session,
        user,
    )

    row = list_tasks(project.id, db_session, user)["tasks"][0]
    assert row["original_obligation_id"] == obligation.id
    assert row["original_obligation_due_date"] is None
    assert row["due_date_adjusted"] is True
