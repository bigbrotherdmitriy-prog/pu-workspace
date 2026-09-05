from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.api.execution_finance as execution_finance
from app.api.execution_finance import (
    BaselineClone,
    ScheduleItemCreate,
    ScheduleProgress,
    StatusUpdate,
    clone_baseline,
    create_schedule_item,
    update_schedule,
    update_status,
)
from app.models.audit_log import AuditLog
from app.models.execution_finance import ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Organization
from app.models.project import Project


def _world(db_session, user_factory):
    organization = Organization(name="Synthetic GPR organization")
    user = user_factory()
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic GPR project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    approved = ScheduleBaseline(
        project_id=project.id,
        contract_id=None,
        created_by_user_id=user.id,
        name="ГПР v1",
        version=1,
        status="approved",
    )
    db_session.add(approved)
    db_session.flush()
    stage = ScheduleItem(
        project_id=project.id,
        baseline_id=approved.id,
        title="Монтаж",
        planned_start=date(2026, 9, 1),
        planned_finish=date(2026, 9, 30),
        planned_progress=100,
        actual_progress=20,
        status="in_progress",
        source_name="synthetic-gpr.csv, строка 2",
        source_excerpt="Монтаж;01.09.2026;30.09.2026",
    )
    db_session.add(stage)
    db_session.flush()
    return user, project, approved, stage


def _allow_roles(monkeypatch):
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: "manager")


def test_gpr_contract_registers_clone_route():
    paths = {route.path for route in execution_finance.router.routes}
    assert "/execution/baselines/{baseline_id}/clone" in paths


def test_clone_creates_a_new_draft_without_rewriting_approved_plan(db_session, user_factory, monkeypatch):
    user, project, approved, stage = _world(db_session, user_factory)
    _allow_roles(monkeypatch)

    result = clone_baseline(
        approved.id,
        BaselineClone(expected_version=1, name="ГПР v2", note="Перенос срока"),
        db_session,
        user,
    )

    clone = db_session.get(ScheduleBaseline, result["id"])
    clone_stage = db_session.scalar(select(ScheduleItem).where(ScheduleItem.baseline_id == clone.id))
    assert result == {"id": clone.id, "version": 2, "status": "draft", "already_created": False}
    assert approved.status == "approved"
    assert clone.name == "ГПР v2"
    assert (clone_stage.title, clone_stage.planned_start, clone_stage.planned_finish) == (
        stage.title,
        stage.planned_start,
        stage.planned_finish,
    )
    assert (clone_stage.actual_start, clone_stage.actual_finish, clone_stage.actual_progress) == (None, None, 0)


def test_clone_replay_returns_the_existing_draft_and_stale_version_is_rejected(db_session, user_factory, monkeypatch):
    user, _project, approved, _stage = _world(db_session, user_factory)
    _allow_roles(monkeypatch)
    payload = BaselineClone(expected_version=1, name="ГПР v2")

    first = clone_baseline(approved.id, payload, db_session, user)
    replay = clone_baseline(approved.id, payload, db_session, user)

    assert replay == {**first, "already_created": True}
    assert db_session.scalar(select(ScheduleBaseline).where(ScheduleBaseline.status == "draft")).id == first["id"]
    with pytest.raises(HTTPException) as error:
        clone_baseline(approved.id, BaselineClone(expected_version=99), db_session, user)
    assert error.value.status_code == 409


def test_approved_and_superseded_baselines_reject_plan_composition_changes(db_session, user_factory, monkeypatch):
    user, _project, approved, _stage = _world(db_session, user_factory)
    _allow_roles(monkeypatch)

    for status in ("approved", "superseded"):
        approved.status = status
        db_session.flush()
        with pytest.raises(HTTPException) as error:
            create_schedule_item(
                ScheduleItemCreate(
                    baseline_id=approved.id,
                    expected_baseline_version=approved.version,
                    title="Новый этап",
                ),
                db_session,
                user,
            )
        assert error.value.status_code == 409


def test_schedule_item_creation_is_version_checked_and_replay_safe(db_session, user_factory, monkeypatch):
    user, _project, approved, _stage = _world(db_session, user_factory)
    _allow_roles(monkeypatch)
    draft_result = clone_baseline(
        approved.id,
        BaselineClone(expected_version=1, name="ГПР v2"),
        db_session,
        user,
    )
    payload = ScheduleItemCreate(
        baseline_id=draft_result["id"],
        expected_baseline_version=2,
        title="Пусконаладка",
        planned_finish="2026-10-15",
        planned_progress=100,
    )

    first = create_schedule_item(payload, db_session, user)
    replay = create_schedule_item(payload, db_session, user)

    assert first["already_created"] is False
    assert replay == {**first, "already_created": True}
    assert len(list(db_session.scalars(select(ScheduleItem).where(
        ScheduleItem.baseline_id == draft_result["id"],
        ScheduleItem.title == "Пусконаладка",
    )))) == 1
    with pytest.raises(HTTPException) as error:
        create_schedule_item(payload.model_copy(update={"expected_baseline_version": 1}), db_session, user)
    assert error.value.status_code == 409


def test_approving_draft_atomically_supersedes_current_plan_and_is_replay_safe(db_session, user_factory, monkeypatch):
    user, _project, approved, _stage = _world(db_session, user_factory)
    _allow_roles(monkeypatch)
    draft_result = clone_baseline(
        approved.id,
        BaselineClone(expected_version=1, name="ГПР v2"),
        db_session,
        user,
    )

    result = update_status(
        "baselines",
        draft_result["id"],
        StatusUpdate(status="approved", expected_status="draft"),
        db_session,
        user,
    )
    replay = update_status(
        "baselines",
        draft_result["id"],
        StatusUpdate(status="approved", expected_status="draft"),
        db_session,
        user,
    )

    assert result["status"] == "approved"
    assert result["superseded_id"] == approved.id
    assert approved.status == "superseded"
    assert replay["already_applied"] is True
    assert db_session.scalar(select(ScheduleBaseline).where(ScheduleBaseline.status == "approved")).id == draft_result["id"]


def test_baseline_approval_requires_manager_role(db_session, user_factory, monkeypatch):
    user, _project, approved, _stage = _world(db_session, user_factory)
    requested_roles = []

    def deny(_db, _user, _project_id, minimum):
        requested_roles.append(minimum)
        raise HTTPException(403, "Insufficient project access")

    monkeypatch.setattr(execution_finance, "require_project_role", deny)
    with pytest.raises(HTTPException) as error:
        update_status(
            "baselines",
            approved.id,
            StatusUpdate(status="approved", expected_status="draft"),
            db_session,
            user,
        )
    assert error.value.status_code == 403
    assert requested_roles == ["manager"]


def test_fact_is_allowed_only_on_current_approved_baseline_and_preserves_plan(db_session, user_factory, monkeypatch):
    user, _project, approved, stage = _world(db_session, user_factory)
    _allow_roles(monkeypatch)
    planned = (stage.title, stage.planned_start, stage.planned_finish, stage.planned_progress)

    result = update_schedule(
        stage.id,
        ScheduleProgress(
            actual_progress=45,
            actual_start="2026-09-03",
            expected_actual_progress=20,
            evidence_ref="evidence:synthetic:stage-1",
        ),
        db_session,
        user,
    )

    assert result["actual_progress"] == 45
    assert (stage.title, stage.planned_start, stage.planned_finish, stage.planned_progress) == planned
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "schedule_actual_updated"))
    assert "evidence_ref=evidence:synthetic:stage-1" in audit.details

    approved.status = "superseded"
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        update_schedule(
            stage.id,
            ScheduleProgress(actual_progress=50, expected_actual_progress=45),
            db_session,
            user,
        )
    assert error.value.status_code == 409


def test_fact_update_has_cas_and_exact_replay_semantics(db_session, user_factory, monkeypatch):
    user, _project, _approved, stage = _world(db_session, user_factory)
    _allow_roles(monkeypatch)
    payload = ScheduleProgress(actual_progress=35, expected_actual_progress=20)

    first = update_schedule(stage.id, payload, db_session, user)
    replay = update_schedule(stage.id, payload, db_session, user)

    assert first["already_applied"] is False
    assert replay["already_applied"] is True
    with pytest.raises(HTTPException) as error:
        update_schedule(
            stage.id,
            ScheduleProgress(actual_progress=50, expected_actual_progress=20),
            db_session,
            user,
        )
    assert error.value.status_code == 409


def test_overview_marks_exactly_one_current_approved_baseline(db_session, user_factory, monkeypatch):
    user, project, approved, _stage = _world(db_session, user_factory)
    _allow_roles(monkeypatch)
    result = execution_finance.overview(project.id, db_session, user)

    assert result["baselines"] == [
        {
            "id": approved.id,
            "contract_id": None,
            "name": "ГПР v1",
            "version": 1,
            "status": "approved",
            "note": None,
            "is_current": True,
        }
    ]
