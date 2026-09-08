from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

import app.api.execution_finance as api
from app.models.audit_log import AuditLog
from app.models.execution_finance import ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def world(db, user_factory):
    user = user_factory()
    org = Organization(name="Synthetic graph company")
    db.add(org); db.flush()
    project = Project(name="Synthetic graph project", organization_id=org.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    baseline = ScheduleBaseline(project_id=project.id, created_by_user_id=user.id,
                                name="Synthetic draft", version=1, status="draft")
    db.add(baseline); db.flush()
    rows = [ScheduleItem(project_id=project.id, baseline_id=baseline.id, title=title)
            for title in ("Synthetic first", "Synthetic second")]
    db.add_all(rows); db.commit()
    return user, baseline, rows


def request(rows, revision=1, **overrides):
    body = dict(expected_graph_revision=revision, project_start="2026-09-01", items=[
        dict(id=rows[0].id, duration_days=3, is_milestone=False, predecessor_ids=None,
             constraint_type="asap", constraint_date=None, not_before_date=None),
        dict(id=rows[1].id, duration_days=2, is_milestone=False, predecessor_ids=f"{rows[0].id}FS",
             constraint_type="asap", constraint_date=None, not_before_date=None),
    ])
    body.update(overrides)
    return api.ScheduleGraphPut.model_validate(body)


def test_graph_persists_intent_and_authoritative_dates(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    result = api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    assert result["graph_revision"] == 2
    assert result["planning_mode"] == "calendar_graph"
    assert result["plan"]["project_finish"] == date(2026, 9, 5)
    db_session.expire_all()
    fresh = api.get_schedule_graph(baseline.id, db_session, user)
    assert fresh == result
    assert fresh["items"][1]["predecessor_ids"] == f"{rows[0].id}FS"
    assert fresh["items"][1]["planned_start"] == date(2026, 9, 4)


def test_legacy_state_preserves_unknown_duration_and_dates(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    rows[0].planned_finish = date(2026, 9, 17)
    db_session.commit()
    result = api.get_schedule_graph(baseline.id, db_session, user)
    assert result["planning_mode"] == "dates_only"
    assert result["project_start"] is None and result["plan"] is None
    assert all(row["duration_days"] is None for row in result["items"])
    assert result["items"][0]["planned_finish"] == date(2026, 9, 17)


@pytest.mark.parametrize("bad", ["cycle", "missing", "foreign", "duplicate", "constraint"])
def test_graph_rejection_is_atomic(db_session, user_factory, bad):
    user, baseline, rows = world(db_session, user_factory)
    body = request(rows).model_dump()
    if bad == "cycle": body["items"][0]["predecessor_ids"] = f"{rows[1].id}FS"
    if bad == "missing": body["items"].pop()
    if bad == "foreign": body["items"][1]["id"] = 99999
    if bad == "duplicate": body["items"][1]["id"] = rows[0].id
    if bad == "constraint":
        body["items"][1].update(constraint_type="mso", constraint_date=date(2026, 9, 1))
    with pytest.raises(HTTPException) as caught:
        api.put_schedule_graph(baseline.id, api.ScheduleGraphPut(**body), db_session, user)
    assert caught.value.status_code == 422
    assert baseline.graph_revision == 1 and baseline.planning_mode == "dates_only"
    assert all(row.planned_start is None for row in rows)
    assert db_session.scalar(select(func.count(AuditLog.id))) == 0


def test_stale_revision_and_approved_mutation_reject(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    with pytest.raises(HTTPException) as caught:
        api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    assert caught.value.status_code == 409
    baseline.status = "approved"; db_session.commit()
    with pytest.raises(HTTPException) as caught:
        api.put_schedule_graph(baseline.id, request(rows, 2), db_session, user)
    assert caught.value.status_code == 409


def test_input_floor_is_not_previous_computed_start(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    api.put_schedule_graph(baseline.id, request(rows, project_start="2026-09-10"), db_session, user)
    result = api.put_schedule_graph(baseline.id, request(rows, 2), db_session, user)
    assert result["items"][1]["planned_start"] == date(2026, 9, 4)


def test_viewer_cannot_write_and_outsider_cannot_read(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    member = db_session.scalar(select(ProjectMember).where(ProjectMember.user_id == user.id))
    member.role = "viewer"; outsider = user_factory(); db_session.commit()
    with pytest.raises(HTTPException) as caught:
        api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    assert caught.value.status_code == 403
    with pytest.raises(HTTPException) as caught:
        api.get_schedule_graph(baseline.id, db_session, outsider)
    assert caught.value.status_code == 403


def test_existing_create_invalidates_revision_and_graph_mode_rejects_legacy_insert(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    api.create_schedule_item(api.ScheduleItemCreate(baseline_id=baseline.id,
        expected_baseline_version=1, title="Synthetic third"), db_session, user)
    assert baseline.graph_revision == 2
    with pytest.raises(HTTPException) as caught:
        api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    assert caught.value.status_code == 409


def test_graph_clone_remaps_edges_and_keeps_source_immutable(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    api.update_status("baselines", baseline.id, api.StatusUpdate(status="approved",
        expected_status="draft", expected_graph_revision=2), db_session, user)
    cloned = api.clone_baseline(baseline.id, api.BaselineClone(expected_version=1), db_session, user)
    result = api.get_schedule_graph(cloned["id"], db_session, user)
    assert result["planning_mode"] == "calendar_graph"
    assert result["items"][1]["predecessor_ids"] == f"{result['items'][0]['id']}FS"
    assert result["items"][0]["id"] != rows[0].id
    assert baseline.status == "approved"


def test_audit_failure_rolls_back_cas_and_all_item_dates(db_session, user_factory, monkeypatch):
    user, baseline, rows = world(db_session, user_factory)
    identifier = baseline.id
    def fail(db, *_args):
        db.flush()  # Exercise failure after item UPDATEs, not only Python state.
        raise RuntimeError("synthetic_audit_failure")
    monkeypatch.setattr(api, "_audit", fail)
    with pytest.raises(RuntimeError, match="synthetic_audit_failure"):
        api.put_schedule_graph(identifier, request(rows), db_session, user)
    db_session.expire_all()
    assert db_session.get(ScheduleBaseline, identifier).graph_revision == 1
    assert all(row.planned_start is None and row.duration_days is None for row in rows)
    assert db_session.scalar(select(func.count(AuditLog.id))) == 0


def test_plan_does_not_modify_actual_facts_or_source(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    rows[0].actual_start = date(2026, 8, 1)
    rows[0].actual_finish = date(2026, 8, 2)
    rows[0].actual_progress = 70
    rows[0].status = "in_progress"
    rows[0].source_excerpt = "Synthetic provenance"
    db_session.commit()
    before = tuple(getattr(rows[0], name) for name in (
        "actual_start", "actual_finish", "actual_progress", "status", "source_excerpt"))
    api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    assert before == tuple(getattr(rows[0], name) for name in (
        "actual_start", "actual_finish", "actual_progress", "status", "source_excerpt"))


def test_graph_mode_rejects_legacy_insert_and_stale_approval(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    with pytest.raises(HTTPException) as caught:
        api.create_schedule_item(api.ScheduleItemCreate(baseline_id=baseline.id,
            expected_baseline_version=1, title="Synthetic new"), db_session, user)
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException) as caught:
        api.update_status("baselines", baseline.id, api.StatusUpdate(status="approved",
            expected_status="draft", expected_graph_revision=1), db_session, user)
    assert caught.value.status_code == 409
    assert baseline.status == "draft" and baseline.graph_revision == 2
    approval = api.StatusUpdate(status="approved", expected_status="draft", expected_graph_revision=2)
    api.update_status("baselines", baseline.id, approval, db_session, user)
    with pytest.raises(HTTPException) as caught:
        api.update_status("baselines", baseline.id, approval, db_session, user)
    assert caught.value.status_code == 409  # Not a false already_applied receipt.


def test_real_import_bumps_once_replay_no_bump_and_graph_mode_rejects(db_session, user_factory):
    from test_v7_schedule_import_validation import _world, _table
    user, project, document, _, baseline = _world(db_session, user_factory,
        _table(("Synthetic first", "", "", ""), ("Synthetic second", "", "", "")))
    payload = api.StructuredImportRequest(project_id=project.id, kind="schedule",
        baseline_id=baseline.id, expected_baseline_version=1, source_rows=[2, 3])
    api.structured_import(document.id, payload, db_session, user)
    assert baseline.graph_revision == 2
    api.structured_import(document.id, payload, db_session, user)
    assert baseline.graph_revision == 2
    rows = list(db_session.scalars(select(ScheduleItem).order_by(ScheduleItem.id)))
    assert len(rows) == 2
    api.put_schedule_graph(baseline.id, request(rows, 2), db_session, user)
    with pytest.raises(HTTPException) as caught:
        api.structured_import(document.id, payload, db_session, user)
    assert caught.value.status_code == 409
    assert baseline.graph_revision == 3
    assert db_session.scalar(select(func.count(ScheduleItem.id))) == 2


def test_http_json_round_trip_uses_actual_graph_routes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    from app.models.user import User
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            def factory():
                row = User(name="Synthetic manager", email="graph@example.test", is_admin=False)
                db.add(row); db.flush(); return row
            user, baseline, rows = world(db, factory)
            application = FastAPI()
            application.include_router(api.router)
            application.dependency_overrides[api.get_db] = lambda: db
            application.dependency_overrides[api.require_user] = lambda: user
            with TestClient(application) as client:
                path = f"/execution/baselines/{baseline.id}/graph"
                response = client.put(path, json=request(rows).model_dump(mode="json"))
                assert response.status_code == 200, response.text
                assert response.json()["items"][1]["planned_start"] == "2026-09-04"
                assert client.get(path).json() == response.json()
    finally:
        engine.dispose()


def test_persistent_stale_actor_is_refreshed_before_permission(db_session, user_factory):
    from sqlalchemy.orm import Session
    from sqlalchemy import update
    user, baseline, rows = world(db_session, user_factory)
    member = db_session.scalar(select(ProjectMember).where(ProjectMember.user_id == user.id))
    assert member.role == "manager"
    with Session(db_session.get_bind()) as other:
        other.execute(update(ProjectMember).where(ProjectMember.id == member.id).values(role="viewer"))
        other.commit()
    with pytest.raises(HTTPException) as caught:
        api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    assert caught.value.status_code == 403


def test_pending_scope_is_not_autoflushed_or_discarded(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    identifier = baseline.id
    rows[0].title = "Uncommitted user edit"
    with pytest.raises(HTTPException) as caught:
        api.get_schedule_graph(identifier, db_session, user)
    assert caught.value.status_code == 409
    assert rows[0].title == "Uncommitted user edit" and rows[0] in db_session.dirty


def test_corrupt_legacy_clone_refuses_before_inserting_draft(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    baseline.status = "approved"
    rows[0].predecessor_ids = "999999FS"
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        api.clone_baseline(baseline.id, api.BaselineClone(expected_version=1), db_session, user)
    assert caught.value.status_code == 409
    assert db_session.scalar(select(func.count(ScheduleBaseline.id))) == 1
    assert db_session.scalar(select(func.count(AuditLog.id))) == 0


def test_corrupt_computed_date_is_not_presented_as_validated_plan(db_session, user_factory):
    user, baseline, rows = world(db_session, user_factory)
    api.put_schedule_graph(baseline.id, request(rows), db_session, user)
    rows[0].planned_finish = date(2026, 10, 1)
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        api.get_schedule_graph(baseline.id, db_session, user)
    assert caught.value.status_code == 409
