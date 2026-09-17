"""§11: confirm_context_bulk defers LLM materialization to a background job;
moving/confirming messages stays synchronous."""
from contextlib import nullcontext

import pytest
from sqlalchemy import select

from app.api import ai_secretary
from app.api.ai_secretary import (
    BulkContextConfirmation,
    _materialize_bulk_job,
    confirm_context_bulk,
    get_materialize_job,
)
from app.models.ai_secretary import Message
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from fastapi import HTTPException


@pytest.fixture
def world(db_session, user_factory, monkeypatch):
    # _materialize_bulk_job opens its own SessionLocal(), matching every other
    # job handler (ocr_batch.reprocess_documents, etc.) -- point it at the
    # same in-memory session the test uses, same pattern as test_ocr_batch.py.
    monkeypatch.setattr(ai_secretary, "SessionLocal", lambda: nullcontext(db_session))
    user = user_factory()
    org = Organization(name="Synthetic Org")
    db_session.add(org)
    db_session.flush()
    project = Project(name="Synthetic Project", organization_id=org.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    db_session.commit()
    return db_session, user, project


def _message(db, org_id, project_id, user_id, text, *, confirmed=False):
    row = Message(
        organization_id=org_id, project_id=project_id, created_by_user_id=user_id,
        source_type="email", source_external_id=f"m-{id(text)}-{text[:10]}", source_name="Письмо",
        content=text, summary="", context_confidence=0.4, context_evidence="",
        context_confirmed=confirmed, status="needs_context_confirmation", attachments_json="[]",
        analysis_required=True,
    )
    db.add(row); db.flush()
    return row


def test_confirm_bulk_confirms_synchronously_but_defers_materialization(world, monkeypatch):
    db, user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    msg1 = _message(db, project.organization_id, project.id, user.id, "Исполнитель обязан подготовить акт.")
    msg2 = _message(db, project.organization_id, project.id, user.id, "Просим подтвердить получение оплаты.")
    db.commit()

    result = confirm_context_bulk(
        BulkContextConfirmation(message_ids=[msg1.id, msg2.id], project_id=project.id),
        db, user,
    )

    # Sync part: messages are confirmed/ready immediately.
    db.refresh(msg1); db.refresh(msg2)
    assert msg1.context_confirmed is True and msg1.status == "ready"
    assert msg2.context_confirmed is True and msg2.status == "ready"
    assert result["confirmed"] == 2

    # Async part: nothing materialized yet, job is queued.
    assert result["already_running"] is False
    assert result["materialization_status"] == "queued"
    job = db.get(BackgroundJob, result["materialization_job_id"])
    assert job.kind == "ai_secretary.materialize_bulk"
    assert job.payload["message_ids"] == [msg1.id, msg2.id]
    assert db.scalar(select(Task).where(Task.message_id == msg1.id)) is None

    # A real worker claim()s the job (status -> running) before invoking the
    # handler; update_cooperative_progress only accepts progress for a job
    # it can see as actually running.
    job.status = "running"; db.commit()
    # Running the job handler produces exactly what the old inline loop did.
    outcome = _materialize_bulk_job(job.payload)
    assert outcome == {"materialized": 2, "total": 2, "cancelled": False}
    task = db.scalar(select(Task).where(Task.message_id == msg1.id))
    assert task is not None and task.extraction_method == "regex"  # no GEMINI_API_KEY in test env


def test_confirm_bulk_guards_against_duplicate_materialization_job(world, monkeypatch):
    db, user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    msg1 = _message(db, project.organization_id, project.id, user.id, "Исполнитель обязан подготовить акт.")
    db.commit()
    first = confirm_context_bulk(
        BulkContextConfirmation(message_ids=[msg1.id], project_id=project.id), db, user,
    )
    assert first["already_running"] is False

    msg2 = _message(db, project.organization_id, project.id, user.id, "Просим подтвердить получение.", confirmed=False)
    db.commit()
    second = confirm_context_bulk(
        BulkContextConfirmation(message_ids=[msg2.id], project_id=project.id), db, user,
    )
    # Same project, previous job still queued (never run) -> no second job created.
    assert second["already_running"] is True
    assert second["materialization_job_id"] == first["materialization_job_id"]
    assert db.scalar(select(BackgroundJob).where(BackgroundJob.kind == "ai_secretary.materialize_bulk")
                     ).id == first["materialization_job_id"]
    assert db.scalar(select(BackgroundJob.id).where(BackgroundJob.kind == "ai_secretary.materialize_bulk")
                     .order_by(BackgroundJob.id.desc())) == first["materialization_job_id"]


def test_materialize_job_is_idempotent_on_retry(world, monkeypatch):
    db, user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    msg1 = _message(db, project.organization_id, project.id, user.id, "Исполнитель обязан подготовить акт.")
    db.commit()
    result = confirm_context_bulk(
        BulkContextConfirmation(message_ids=[msg1.id], project_id=project.id), db, user,
    )
    job = db.get(BackgroundJob, result["materialization_job_id"])
    job.status = "running"; db.commit()
    _materialize_bulk_job(job.payload)
    job.status = "running"; db.commit()  # simulate a second worker claim on redelivery/retry
    _materialize_bulk_job(job.payload)
    assert len(list(db.scalars(select(Task).where(Task.message_id == msg1.id)))) == 1


def test_get_materialize_job_requires_viewer_role_and_normalizes_status(world, monkeypatch):
    db, user, project = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    msg1 = _message(db, project.organization_id, project.id, user.id, "Исполнитель обязан подготовить акт.")
    db.commit()
    result = confirm_context_bulk(
        BulkContextConfirmation(message_ids=[msg1.id], project_id=project.id), db, user,
    )
    job_id = result["materialization_job_id"]

    status = get_materialize_job(job_id, db, user)
    assert status["status"] == "queued"
    assert status["progress"] == 0

    job = db.get(BackgroundJob, job_id)
    job.status = "running"; db.commit()
    _materialize_bulk_job(job.payload)
    job.status = "completed"
    job.result = {**dict(job.result or {})}
    db.commit()
    status_after = get_materialize_job(job_id, db, user)
    assert status_after["status"] == "succeeded"  # "completed" is normalized like the OCR-batch endpoint

    with pytest.raises(HTTPException) as missing:
        get_materialize_job(job_id + 999, db, user)
    assert missing.value.status_code == 404
