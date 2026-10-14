import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.responses import bulk_dismiss, bulk_dismiss_undo
from app.api.responses import project_router as drafts_project_router
from app.api.tasks import bulk_close, bulk_close_undo
from app.api.tasks import project_router as tasks_project_router
from app.backlog_triage import BulkFilter, BulkRequest
from app.models.audit_log import AuditLog
from app.models.management import ManagementHistory
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task, TaskDueDateHistory, TaskHistory

OLD = datetime.now(timezone.utc) - timedelta(days=45)
NEW = datetime.now(timezone.utc) - timedelta(days=2)


def _world(db, user_factory, role="owner"):
    user = user_factory()
    org = Organization(name="Synthetic organization")
    db.add(org); db.flush()
    project = Project(name="Synthetic project", organization_id=org.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    db.commit()
    return user, project


def _task(db, project, user, n, *, created=OLD, status="assigned", source_type="document_analysis",
          needs_review=True, external="proposed"):
    task = Task(
        project_id=project.id, assignee_user_id=user.id, created_by_user_id=user.id,
        title=f"Задача {n}", status=status, source_type=source_type, source_file_id=f"file-{n}",
        source_file_name="Договор.pdf", source_excerpt="фрагмент", source_excerpt_hash=f"{n:064d}",
        confidence=0.5, needs_review=needs_review, external_action_status=external, created_at=created,
    )
    db.add(task); db.flush()
    return task


def _draft(db, project, user, n, *, created=OLD, status="draft", source_file_id=None, source_file_name="Письмо",
           message_id=None, revision=1, approved_revision=None):
    draft = ResponseDraft(
        project_id=project.id, reviewer_user_id=user.id, subject=f"Ответ {n}", body="Текст", status=status,
        source_file_id=source_file_id or f"drive-file-{n}", source_file_name=source_file_name,
        source_excerpt="фрагмент", source_excerpt_hash=f"{n:064d}", confidence=0.5, created_at=created,
        message_id=message_id, revision=revision, approved_revision=approved_revision,
    )
    db.add(draft); db.flush()
    return draft


PRESET = BulkFilter(older_than_days=30, untouched_only=True, auto_extracted_only=True)


def test_routes_are_registered_under_project_prefix():
    task_paths = {route.path for route in tasks_project_router.routes}
    draft_paths = {route.path for route in drafts_project_router.routes}
    assert "/projects/{project_id}/tasks/bulk-close" in task_paths
    assert "/projects/{project_id}/tasks/bulk-close/{batch_id}/undo" in task_paths
    assert "/projects/{project_id}/drafts/bulk-dismiss" in draft_paths
    assert "/projects/{project_id}/drafts/bulk-dismiss/{batch_id}/undo" in draft_paths


def test_task_dry_run_counts_only_matching_tasks_and_writes_nothing(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    match = [_task(db_session, project, user, n) for n in range(1, 4)]
    _task(db_session, project, user, 10, created=NEW)                        # too new
    _task(db_session, project, user, 11, source_type="manual")               # not auto-extracted
    _task(db_session, project, user, 12, needs_review=False)                 # reviewed
    _task(db_session, project, user, 13, status="completed")                 # not open
    _task(db_session, project, user, 14, status="in_progress")               # touched (status changed)
    _task(db_session, project, user, 15, external="queued")                  # external action live
    edited = _task(db_session, project, user, 16)                             # human history
    db_session.add(TaskHistory(task_id=edited.id, action="updated", old_status="assigned",
                               new_status="assigned", changed_by_user_id=user.id))
    moved = _task(db_session, project, user, 17)                              # due date moved
    db_session.add(TaskDueDateHistory(task_id=moved.id, old_due_date=None, new_due_date=date.today(),
                                      reason="перенос", changed_by_user_id=user.id))
    reassigned = _task(db_session, project, user, 18)                         # management history
    db_session.add(ManagementHistory(organization_id=project.organization_id, project_id=project.id,
                                     entity_type="task", entity_id=reassigned.id, record_version=2,
                                     action="updated", actor_user_id=user.id, old_values={}, new_values={}))
    db_session.commit()

    result = bulk_close(project.id, BulkRequest(filter=PRESET, dry_run=True), db_session, user)

    assert result["dry_run"] is True
    assert result["count"] == 3
    assert [row["id"] for row in result["sample"]] == [task.id for task in match]
    assert result["sample"][0]["title"] == "Задача 1"
    assert db_session.scalar(select(AuditLog.id)) is None
    assert {task.status for task in match} == {"assigned"}


def test_task_filters_source_type_alias_ids_and_sample_cap(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    tasks = [_task(db_session, project, user, n, source_type="local_upload") for n in range(1, 26)]
    _task(db_session, project, user, 40, source_type="email")
    db_session.commit()

    by_doc = bulk_close(project.id, BulkRequest(filter=BulkFilter(source_type="document"), dry_run=True), db_session, user)
    assert by_doc["count"] == 25 and len(by_doc["sample"]) == 20
    by_email = bulk_close(project.id, BulkRequest(filter=BulkFilter(source_type="email"), dry_run=True), db_session, user)
    assert by_email["count"] == 1
    picked = bulk_close(project.id, BulkRequest(filter=BulkFilter(task_ids=[tasks[0].id, tasks[1].id]), dry_run=True),
                        db_session, user)
    assert picked["count"] == 2
    empty_ids = bulk_close(project.id, BulkRequest(filter=BulkFilter(task_ids=[]), dry_run=True), db_session, user)
    assert empty_ids["count"] == 0


def test_non_member_and_viewer_are_denied(db_session, user_factory):
    owner, project = _world(db_session, user_factory)
    _task(db_session, project, owner, 1)
    stranger = user_factory()
    viewer = user_factory()
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
    db_session.commit()

    for call in (
        lambda u: bulk_close(project.id, BulkRequest(filter=PRESET, dry_run=True), db_session, u),
        lambda u: bulk_dismiss(project.id, BulkRequest(filter=PRESET, dry_run=True), db_session, u),
        lambda u: bulk_close_undo(project.id, 1, db_session, u),
        lambda u: bulk_dismiss_undo(project.id, 1, db_session, u),
    ):
        for user in (stranger, viewer):
            with pytest.raises(HTTPException) as error:
                call(user)
            assert error.value.status_code == 403


def test_editor_may_preview_but_not_close(db_session, user_factory):
    owner, project = _world(db_session, user_factory)
    _task(db_session, project, owner, 1)
    editor = user_factory()
    db_session.add(ProjectMember(project_id=project.id, user_id=editor.id, role="editor"))
    db_session.commit()
    assert bulk_close(project.id, BulkRequest(filter=PRESET, dry_run=True), db_session, editor)["count"] == 1
    with pytest.raises(HTTPException) as error:
        bulk_close(project.id, BulkRequest(filter=PRESET, dry_run=False, reason="разбор"), db_session, editor)
    assert error.value.status_code == 403


def test_real_close_cancels_writes_audit_and_undo_restores_exactly(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    a = _task(db_session, project, user, 1)
    b = _task(db_session, project, user, 2, status="in_progress")
    keep = _task(db_session, project, user, 3)
    db_session.commit()
    versions = {a.id: a.record_version, b.id: b.record_version}

    with pytest.raises(HTTPException) as error:
        bulk_close(project.id, BulkRequest(filter=BulkFilter(task_ids=[a.id, b.id]), dry_run=False, reason=" "),
                   db_session, user)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        bulk_close(project.id, BulkRequest(filter=BulkFilter(task_ids=[a.id, b.id]), dry_run=False,
                                           reason="разбор", expected_count=5), db_session, user)
    assert error.value.status_code == 409

    result = bulk_close(project.id, BulkRequest(filter=BulkFilter(task_ids=[a.id, b.id]), dry_run=False,
                                                reason="Разбор очереди"), db_session, user)
    assert result["count"] == 2 and result["target_status"] == "cancelled"
    db_session.refresh(a); db_session.refresh(b); db_session.refresh(keep)
    assert (a.status, b.status, keep.status) == ("cancelled", "cancelled", "assigned")
    assert a.completed_at is None
    batch = db_session.get(AuditLog, result["batch_id"])
    assert batch.action == "task_bulk_close"
    details = json.loads(batch.details)
    assert details["reason"] == "Разбор очереди"
    assert {item["id"]: item["status"] for item in details["items"]} == {a.id: "assigned", b.id: "in_progress"}
    per_task = db_session.scalars(select(AuditLog).where(AuditLog.action == "task_bulk_closed")).all()
    assert {row.entity_id for row in per_task} == {a.id, b.id}
    history = db_session.scalars(select(TaskHistory).where(TaskHistory.action == "bulk_closed")).all()
    assert len(history) == 2 and "Разбор очереди" in history[0].details
    assert db_session.scalars(select(ManagementHistory).where(ManagementHistory.action == "bulk_closed")).all()

    undo = bulk_close_undo(project.id, result["batch_id"], db_session, user)
    assert undo == {"batch_id": result["batch_id"], "restored": 2, "skipped": []}
    db_session.refresh(a); db_session.refresh(b)
    assert (a.status, b.status) == ("assigned", "in_progress")
    assert a.record_version == versions[a.id] + 2
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "task_bulk_close_undo")).entity_id == result["batch_id"]
    # The preset sees the restored task as untouched again (bulk rows don't count).
    assert bulk_close(project.id, BulkRequest(filter=PRESET, dry_run=True), db_session, user)["count"] == 2

    with pytest.raises(HTTPException) as error:
        bulk_close_undo(project.id, result["batch_id"], db_session, user)
    assert error.value.status_code == 409


def test_undo_keeps_later_human_decision(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    a = _task(db_session, project, user, 1)
    b = _task(db_session, project, user, 2)
    db_session.commit()
    result = bulk_close(project.id, BulkRequest(filter=PRESET, dry_run=False, reason="разбор"), db_session, user)
    b.status = "in_progress"; b.record_version += 1
    db_session.commit()
    undo = bulk_close_undo(project.id, result["batch_id"], db_session, user)
    assert undo["restored"] == 1 and undo["skipped"] == [b.id]
    db_session.refresh(a); db_session.refresh(b)
    assert (a.status, b.status) == ("assigned", "in_progress")


def test_undo_of_foreign_project_batch_is_not_found(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    _task(db_session, project, user, 1)
    db_session.commit()
    result = bulk_close(project.id, BulkRequest(filter=PRESET, dry_run=False, reason="разбор"), db_session, user)
    _, other = _world(db_session, user_factory)
    db_session.add(ProjectMember(project_id=other.id, user_id=user.id, role="owner"))
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        bulk_close_undo(other.id, result["batch_id"], db_session, user)
    assert error.value.status_code == 404


def test_draft_dry_run_never_includes_approved_sent_or_queued(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    plain = [_draft(db_session, project, user, n) for n in range(1, 3)]
    protected = [
        _draft(db_session, project, user, 10, status="approved", approved_revision=1),
        _draft(db_session, project, user, 11, status="sent"),
        _draft(db_session, project, user, 12, status="queued"),
        _draft(db_session, project, user, 13, status="sending"),
        _draft(db_session, project, user, 14, status="unknown"),
        _draft(db_session, project, user, 15, status="failed"),
    ]
    _draft(db_session, project, user, 20, created=NEW)                                        # too new
    _draft(db_session, project, user, 21, source_file_id="mail-draft:x", source_file_name="mail-client")  # written by a person
    _draft(db_session, project, user, 22, revision=2)                                         # edited
    reviewed = _draft(db_session, project, user, 23)
    db_session.add(AuditLog(action="response_draft_edited", entity_type="response_draft", entity_id=reviewed.id))
    db_session.commit()

    preview = bulk_dismiss(project.id, BulkRequest(filter=PRESET, dry_run=True), db_session, user)
    assert preview["count"] == 2
    assert [row["title"] for row in preview["sample"]] == ["Ответ 1", "Ответ 2"]

    everything = bulk_dismiss(project.id, BulkRequest(filter=BulkFilter(), dry_run=False, reason="разбор"),
                              db_session, user)
    assert set(everything["draft_ids"]).isdisjoint({draft.id for draft in protected})
    for draft in protected:
        db_session.refresh(draft)
    assert [d.status for d in protected] == ["approved", "sent", "queued", "sending", "unknown", "failed"]
    picked = bulk_dismiss(project.id, BulkRequest(filter=BulkFilter(draft_ids=[d.id for d in protected]), dry_run=True),
                          db_session, user)
    assert picked["count"] == 0
    assert plain[0].status == "rejected"


def test_draft_source_type_filter_and_undo(db_session, user_factory):
    user, project = _world(db_session, user_factory)
    # SQLite does not enforce the FK; only "has a source message" matters here.
    by_mail = _draft(db_session, project, user, 1, message_id=987654)
    by_doc = _draft(db_session, project, user, 2)
    db_session.commit()

    assert bulk_dismiss(project.id, BulkRequest(filter=BulkFilter(source_type="email"), dry_run=True),
                        db_session, user)["count"] == 1
    assert bulk_dismiss(project.id, BulkRequest(filter=BulkFilter(source_type="document"), dry_run=True),
                        db_session, user)["count"] == 1
    with pytest.raises(HTTPException):
        bulk_dismiss(project.id, BulkRequest(filter=BulkFilter(source_type="fax"), dry_run=True), db_session, user)

    result = bulk_dismiss(project.id, BulkRequest(filter=PRESET, dry_run=False, reason="Разбор черновиков"),
                          db_session, user)
    assert result["count"] == 2
    db_session.refresh(by_mail); db_session.refresh(by_doc)
    assert (by_mail.status, by_doc.status) == ("rejected", "rejected")
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "response_draft_bulk_dismissed")).all()
    assert {row.entity_id for row in audit} == {by_mail.id, by_doc.id}

    undo = bulk_dismiss_undo(project.id, result["batch_id"], db_session, user)
    assert undo["restored"] == 2
    db_session.refresh(by_mail); db_session.refresh(by_doc)
    assert (by_mail.status, by_doc.status) == ("draft", "draft")
    assert bulk_dismiss(project.id, BulkRequest(filter=PRESET, dry_run=True), db_session, user)["count"] == 2


def test_bulk_routes_are_mounted_on_the_app():
    from app.main import app

    def leaves(routes):
        # include_router() is lazy in this FastAPI version (see test_mvp1_google_storage_oauth).
        for route in routes:
            expand = getattr(route, "effective_route_contexts", None)
            if callable(expand):
                yield from expand()
            else:
                yield route

    paths = {(route.path, tuple(sorted(getattr(route, "methods", ()) or ()))) for route in leaves(app.routes)}
    assert ("/projects/{project_id}/tasks/bulk-close", ("POST",)) in paths
    assert ("/projects/{project_id}/tasks/bulk-close/{batch_id}/undo", ("POST",)) in paths
    assert ("/projects/{project_id}/drafts/bulk-dismiss", ("POST",)) in paths
    assert ("/projects/{project_id}/drafts/bulk-dismiss/{batch_id}/undo", ("POST",)) in paths
