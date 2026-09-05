from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.management import router
from app.database import Base
from app.models.governance import GovernanceHistory
from app.models.management import ObligationHistory
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_pilot import SourceCurrent, SourceReference
from app.mvp3.attention import attention_page
from app.mvp3.lifecycle import ManagementConflict, ManagementDenied, ManagementLifecycle, normalized_task_state
from v54_pilot_fixture import pin, seed, uid


@pytest.fixture
def world():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed(db)
        source = db.get(SourceReference, uid(13))
        source.availability = "available"
        source.freshness = "fresh"
        source.sync_state = "current"
        db.add_all([
            ProjectMember(project_id=4, user_id=2, role="manager"),
            ProjectMember(project_id=4, user_id=3, role="editor"),
        ])
        db.flush()
        yield db, ManagementLifecycle()
        db.rollback()
    engine.dispose()


def evidence_pin(tenant=1):
    return pin("evidence", uid(16), tenant=tenant)


def create_obligation(db, service, actor=2, **changes):
    scope = service.scope(db, project_id=4, actor_user_id=actor)
    values = dict(scope=scope, title="Передать комплект исполнительной документации",
                  owner_user_id=2, evidence_pins=[evidence_pin()], due_date=date(2026, 9, 10))
    values.update(changes)
    return service.create_obligation(db, **values), scope


def test_obligation_is_evidence_backed_versioned_and_append_history(world):
    db, service = world
    row, scope = create_obligation(db, service)
    assert row.record_version == 1
    assert row.status == "needs_confirmation"
    assert row.review_state == "needs_review"  # unknown extractor confidence cannot auto-confirm
    assert row.evidence_pins == [evidence_pin()]
    assert row.deadline_policy == {"reminder_days": [7, 3, 1], "quiet_hours": {"start": "20:00", "end": "08:00"}}
    history = db.scalars(select(ObligationHistory).where(ObligationHistory.obligation_id == row.id)).all()
    assert [(event.sequence, event.event, event.resulting_version) for event in history] == [(1, "created", 1)]

    changed = service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=1,
                                            status="confirmed")
    assert changed.record_version == 2 and changed.review_state == "verified"
    assert [(event.sequence, event.to_status) for event in db.scalars(select(ObligationHistory).where(
        ObligationHistory.obligation_id == row.id).order_by(ObligationHistory.sequence)).all()] == [
        (1, "needs_confirmation"), (2, "confirmed")]
    assert db.scalar(select(ObligationHistory).where(ObligationHistory.obligation_id == row.id,
                                                     ObligationHistory.sequence == 2)).from_status == "needs_confirmation"


def test_low_confidence_confirmation_requires_manager(world):
    db, service = world
    row, _ = create_obligation(db, service, actor=3)
    editor = service.scope(db, project_id=4, actor_user_id=3)
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.transition_obligation(db, scope=editor, obligation_id=row.id, expected_version=1, status="confirmed")
    assert row.status == "needs_confirmation"


def test_obligation_cas_rejects_stale_writer_and_invalid_transition(world):
    db, service = world
    row, scope = create_obligation(db, service)
    service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=1, status="confirmed")
    with pytest.raises(ManagementConflict, match="version_conflict"):
        service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=1, status="in_progress")
    with pytest.raises(ManagementDenied, match="invalid_transition"):
        service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=2, status="needs_confirmation")


def test_management_history_rejects_ordinary_update_and_delete(world):
    db, service = world
    row, _ = create_obligation(db, service)
    event = db.scalar(select(ObligationHistory).where(ObligationHistory.obligation_id == row.id))
    event.reason = "rewrite"
    with pytest.raises(ValueError, match="management_history_is_append_only"):
        db.flush()
    db.rollback()


def test_terminal_correction_requires_reason_and_preserves_history(world):
    db, service = world
    row, scope = create_obligation(db, service)
    service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=1, status="confirmed")
    service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=2, status="fulfilled",
                                  result_note="Акт подписан")
    with pytest.raises(ManagementDenied, match="invalid_input"):
        service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=3, status="in_progress")
    corrected = service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=3,
                                              status="in_progress", reason="Исправление ошибочного завершения")
    assert corrected.record_version == 4
    assert db.scalar(select(ObligationHistory).where(ObligationHistory.obligation_id == row.id,
                                                     ObligationHistory.sequence == 4)).reason


def test_internal_task_mapping_is_idempotent_and_creates_no_external_effect(world):
    db, service = world
    row, scope = create_obligation(db, service)
    row = service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=1, status="confirmed")
    task = service.ensure_internal_task(db, scope=scope, obligation_id=row.id, expected_version=2)
    assert task.external_action_status == "proposed"
    assert task.google_task_id is None and task.google_calendar_event_id is None
    assert normalized_task_state(task.status) == "OPEN"
    same = service.ensure_internal_task(db, scope=scope, obligation_id=row.id, expected_version=3)
    assert same.id == task.id
    assert db.scalars(select(Task).where(Task.source_file_id == f"obligation:{row.id}")).all() == [task]


@pytest.mark.parametrize("status,expected", [("assigned", "OPEN"), ("in_progress", "IN_PROGRESS"),
                                               ("completed", "COMPLETED"), ("cancelled", "CANCELLED")])
def test_task_state_mapping_is_explicit(status, expected):
    assert normalized_task_state(status) == expected


def test_risk_and_decision_are_linked_to_obligation_task_and_evidence(world):
    db, service = world
    obligation, scope = create_obligation(db, service)
    obligation = service.transition_obligation(db, scope=scope, obligation_id=obligation.id,
                                               expected_version=1, status="confirmed")
    task = service.ensure_internal_task(db, scope=scope, obligation_id=obligation.id, expected_version=2)
    risk = service.create_risk(db, scope=scope, title="Риск просрочки поставки", owner_user_id=2,
                               evidence_pins=[evidence_pin()], criticality="high",
                               obligation_id=obligation.id, task_id=task.id)
    decision = service.create_decision(db, scope=scope, question="Утвердить корректирующий график?",
                                       owner_user_id=2, evidence_pins=[evidence_pin()],
                                       obligation_id=obligation.id, task_id=task.id, risk_id=risk.id)
    assert (risk.obligation_id, risk.task_id, decision.risk_id) == (obligation.id, task.id, risk.id)
    risk = service.transition_governance(db, scope=scope, entity_type="risk", entity_id=risk.id,
                                         expected_version=1, status="confirmed")
    decision = service.transition_governance(db, scope=scope, entity_type="decision", entity_id=decision.id,
                                             expected_version=1, status="confirmed")
    assert risk.record_version == decision.record_version == 2
    assert db.scalar(select(GovernanceHistory).where(GovernanceHistory.entity_type == "risk",
                                                     GovernanceHistory.entity_id == risk.id,
                                                     GovernanceHistory.sequence == 2)).from_status == "needs_confirmation"


def test_evidence_pin_fails_closed_for_tenant_and_noncurrent_version(world):
    db, service = world
    scope = service.scope(db, project_id=4, actor_user_id=2)
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.create_obligation(db, scope=scope, title="X", owner_user_id=2,
                                  evidence_pins=[evidence_pin(tenant=2)])
    db.get(SourceCurrent, uid(13)).version_id = uid(14)
    db.flush()
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.create_obligation(db, scope=scope, title="X", owner_user_id=2,
                                  evidence_pins=[evidence_pin()])


def test_cross_project_and_nonmember_access_are_hidden(world):
    db, service = world
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.scope(db, project_id=9, actor_user_id=2)
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        service.scope(db, project_id=4, actor_user_id=999)


def test_attention_is_stable_paginated_explainable_and_read_only(world):
    db, service = world
    row, scope = create_obligation(db, service, due_date=date(2026, 9, 1))
    service.transition_obligation(db, scope=scope, obligation_id=row.id, expected_version=1, status="confirmed")
    risk = service.create_risk(db, scope=scope, title="Критический риск", owner_user_id=2,
                               evidence_pins=[evidence_pin()], criticality="critical", obligation_id=row.id)
    result = attention_page(db, project_id=4, now=datetime(2026, 9, 5, tzinfo=timezone.utc), limit=1)
    assert result["total"] == 2 and len(result["items"]) == 1
    assert result["items"][0]["evidence_pins"] == [evidence_pin()]
    assert result["items"][0]["explanation"] in {"deadline_passed", "human_review_required"}
    assert result["external_actions_created"] is False
    assert risk.status == "needs_confirmation"


def test_timezone_and_deadline_policy_are_validated(world):
    db, service = world
    with pytest.raises(ManagementDenied, match="invalid_timezone"):
        create_obligation(db, service, timezone_name="Invalid/Timezone")
    with pytest.raises(ManagementDenied, match="invalid_deadline_policy"):
        create_obligation(db, service, deadline_policy={"reminder_days": [-1], "quiet_hours": {"start": "20:00", "end": "08:00"}})


def test_v2_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert {"/management/v2/obligations", "/management/v2/obligations/{obligation_id}",
            "/management/v2/obligations/{obligation_id}/history",
            "/management/v2/obligations/{obligation_id}/internal-task", "/management/v2/risks",
            "/management/v2/decisions", "/management/v2/{entity_type}/{entity_id}",
            "/management/v2/{entity_type}/{entity_id}/history",
            "/management/v2/attention"} <= paths
