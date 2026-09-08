"""Real local engines; fault injection never invokes provider integrations."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.api import ai_secretary as ai
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.governance import Risk, Decision
from app.models.management import Obligation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task


@pytest.fixture
def context_world(db_session, user_factory, monkeypatch):
    user = user_factory()
    org = Organization(name="Recovery test")
    db_session.add(org)
    db_session.flush()
    project = Project(name="Recovery project", organization_id=org.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    db_session.commit()
    # Payload describes a proposed action only. All extraction engines stay real.
    monkeypatch.setattr(ai, "configured_action_adapter", lambda *_: SimpleNamespace(provider="synthetic"))
    value = ai.ingest_message(ai.IncomingMessage(
        project_id=project.id, source_type="email", source_name="Test request",
        source_external_id="context-recovery",
        content="Просим подготовить отчёт до 15.10.2026. Риск критичной просрочки поставки. Требуется решение согласовать вариант.",
        routing_confidence=0.4, routing_evidence="Ambiguous test context",
    ), db_session, user)
    return SimpleNamespace(message_id=value["id"], project_id=project.id, user=user)


def _counts(db):
    return tuple(db.scalar(select(func.count()).select_from(model))
                 for model in (Task, Obligation, ResponseDraft, Risk, Decision))


def _confirm(db, world):
    return ai.confirm_context(world.message_id, ai.ContextConfirmation(project_id=world.project_id), db, world.user)


@pytest.mark.parametrize("boundary", ["after_tasks", "after_drafts", "before_checkpoint"])
def test_failure_before_checkpoint_rolls_back_all_effects_and_retry_links_them(
    db_session, context_world, monkeypatch, boundary,
):
    assert _counts(db_session) == (0, 0, 0, 0, 0)
    target = {"after_tasks": "create_response_drafts", "after_drafts": "create_governance_items",
              "before_checkpoint": "brief_summary"}[boundary]
    original = getattr(ai, target)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected analysis boundary")

    monkeypatch.setattr(ai, target, fail)
    with pytest.raises(RuntimeError, match="injected analysis boundary"):
        _confirm(db_session, context_world)
    db_session.rollback()
    assert _counts(db_session) == (0, 0, 0, 0, 0)
    assert db_session.get(Message, context_world.message_id).analysis_required
    monkeypatch.setattr(ai, target, original)
    first = _confirm(db_session, context_world)
    assert first["tasks"] and len(first["drafts"]) == len(first["risks"]) == 1
    counts = _counts(db_session)
    replay = _confirm(db_session, context_world)
    assert _counts(db_session) == counts
    assert [row["id"] for row in replay["tasks"]] == [row["id"] for row in first["tasks"]]
    assert all(row.message_id == context_world.message_id and row.external_action_status == "proposed"
               for row in db_session.scalars(select(Task)))
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "message_analysis_materialized")) == 1


def test_stale_loaded_message_does_not_repeat_completed_analysis(db_session, context_world):
    stale = db_session.get(Message, context_world.message_id)
    stale.context_confirmed = True
    db_session.commit()
    assert stale.analysis_required
    with Session(db_session.get_bind()) as winner:
        _confirm(winner, context_world)
    assert stale.analysis_required
    ai._analyze_confirmed_message(db_session, stale)
    db_session.commit()
    assert not stale.analysis_required
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "message_analysis_materialized")) == 1


def test_outer_commit_failure_rolls_back_checkpoint_and_all_children(db_session, context_world):
    def refuse_commit(_session):
        raise RuntimeError("outer commit failed")

    event.listen(db_session, "before_commit", refuse_commit)
    try:
        with pytest.raises(RuntimeError, match="outer commit failed"):
            _confirm(db_session, context_world)
    finally:
        event.remove(db_session, "before_commit", refuse_commit)
        db_session.rollback()
    assert _counts(db_session) == (0, 0, 0, 0, 0)
    row = db_session.get(Message, context_world.message_id)
    assert row.analysis_required and not row.context_confirmed
    assert _confirm(db_session, context_world)["tasks"]


def test_analysis_does_not_commit_or_lose_unrelated_caller_writes(db_session, context_world):
    row = db_session.get(Message, context_world.message_id)
    row.context_confirmed = True
    db_session.commit()
    caller = AuditLog(action="test_caller_pending", entity_type="message", entity_id=row.id, details="test")
    db_session.add(caller)
    tasks, drafts, risks, _ = ai._analyze_confirmed_message(db_session, row)
    assert tasks and drafts and risks
    assert tasks[0].message_id == row.id and drafts[0].message_id == row.id
    assert db_session.in_transaction()
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "test_caller_pending")) is caller
    db_session.rollback()
    assert _counts(db_session) == (0, 0, 0, 0, 0)
    assert row.analysis_required
    assert not db_session.scalar(select(AuditLog.id).where(AuditLog.action == "test_caller_pending"))


@pytest.mark.parametrize("pending", ["edit", "delete"])
def test_pending_message_state_is_not_silently_refreshed(db_session, context_world, pending):
    row = db_session.get(Message, context_world.message_id)
    if pending == "edit":
        row.context_evidence = "Caller edit must survive denial"
    else:
        db_session.delete(row)
    with pytest.raises(HTTPException) as caught:
        _confirm(db_session, context_world)
    assert caught.value.status_code == 409
    if pending == "edit":
        assert row.context_evidence == "Caller edit must survive denial" and row in db_session.dirty
    else:
        assert row in db_session.deleted
    with db_session.no_autoflush:
        assert _counts(db_session) == (0, 0, 0, 0, 0)


def test_bulk_failure_rolls_back_every_message(db_session, context_world, monkeypatch):
    original = db_session.get(Message, context_world.message_id)
    second = Message(organization_id=original.organization_id, project_id=original.project_id,
        created_by_user_id=original.created_by_user_id, source_type="email", source_external_id="second-request",
        source_name="Second request", content="Просим подготовить другой документ до 16.10.2026.",
        summary="Pending", context_evidence="Ambiguous", context_confirmed=False, analysis_required=True)
    db_session.add(second)
    db_session.commit()
    ids = [original.id, second.id]
    analyze = ai._analyze_confirmed_message
    def fail_second(db, row):
        if row.id == second.id:
            raise RuntimeError("second message failed")
        return analyze(db, row)
    monkeypatch.setattr(ai, "_analyze_confirmed_message", fail_second)
    payload = ai.BulkContextConfirmation(message_ids=ids, project_id=context_world.project_id)
    with pytest.raises(RuntimeError, match="second message failed"):
        ai.confirm_context_bulk(payload, db_session, context_world.user)
    db_session.rollback()
    assert _counts(db_session) == (0, 0, 0, 0, 0)
    assert all(row.analysis_required and not row.context_confirmed for row in db_session.scalars(select(Message)))
    monkeypatch.setattr(ai, "_analyze_confirmed_message", analyze)
    ai.confirm_context_bulk(payload, db_session, context_world.user)
    counts = _counts(db_session)
    ai.confirm_context_bulk(payload, db_session, context_world.user)
    assert _counts(db_session) == counts
    assert set(db_session.scalars(select(Task.message_id))) == set(ids)
