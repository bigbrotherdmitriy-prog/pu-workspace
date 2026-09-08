"""Synthetic complete-graph row lifecycle, not a second scheduler."""
from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

import app.api.execution_finance as api
from app.models.audit_log import AuditLog
from app.models.execution_finance import BudgetLine, CashFlowEntry, ScheduleItem
from app.models.project_member import ProjectMember
from test_v7_schedule_graph_api import world, request


def row_body(row=None, **changes):
    body = dict(id=row.id if row else None, client_ref=None if row else "new_work",
                title="Synthetic work", duration_days=2, is_milestone=False,
                constraint_type="asap", constraint_date=None, not_before_date=None, dependencies=[])
    body.update(changes)
    return body


def body_for(rows, **changes):
    body = dict(expected_graph_revision=1, project_start="2026-09-01",
                deleted_ids=[], items=[row_body(row) for row in rows])
    body.update(changes)
    return body


def invoke(db, actor, baseline, body):
    return api.put_schedule_graph_rows(baseline.id, api.ScheduleGraphRowsPut.model_validate(body), db, actor)


def test_add_rename_and_new_dependency_ids_are_persisted(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    api.put_schedule_graph(baseline.id, request(rows), db_session, actor)
    body = body_for(rows, expected_graph_revision=2)
    body["items"][0]["title"] = "Renamed synthetic work"
    body["items"] += [row_body(client_ref="first_new"), row_body(client_ref="second_new", dependencies=[
        dict(predecessor_ref="first_new", link_type="FS", lag_days=1)])]
    result = invoke(db_session, actor, baseline, body)
    assert result["graph_revision"] == 3
    assert result["items"][0]["id"] == rows[0].id
    assert result["items"][0]["title"] == "Renamed synthetic work"
    mapping = result.pop("client_ref_map")
    assert set(mapping) == {"first_new", "second_new"}
    by_id = {row["id"]: row for row in result["items"]}
    assert by_id[mapping["second_new"]]["predecessor_ids"] == f'{mapping["first_new"]}FS+1d'
    assert by_id[mapping["second_new"]]["planned_start"] == date(2026, 9, 4)
    db_session.expire_all()
    assert api.get_schedule_graph(baseline.id, db_session, actor) == result
    assert db_session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "schedule_graph_rows_saved")) == 1
    with pytest.raises(HTTPException) as caught:
        invoke(db_session, actor, baseline, body)
    assert caught.value.status_code == 409
    assert db_session.scalar(select(func.count(ScheduleItem.id))) == 4


@pytest.mark.parametrize("bad", ["omission", "foreign", "duplicate", "delete_keep", "duplicate_delete",
                                  "unknown_ref", "cycle", "deleted_dependency", "duplicate_ref"])
def test_invalid_batch_is_atomic(db_session, user_factory, bad):
    actor, baseline, rows = world(db_session, user_factory)
    body = body_for(rows)
    body["items"].append(row_body())
    if bad == "omission": body["items"].pop(0)
    if bad == "foreign": body["items"][0]["id"] = 99999
    if bad == "duplicate": body["items"][1]["id"] = rows[0].id
    if bad == "delete_keep": body["deleted_ids"] = [rows[0].id]
    if bad == "duplicate_delete":
        body["items"].pop(0); body["deleted_ids"] = [rows[0].id] * 2
    if bad == "unknown_ref": body["items"][0]["dependencies"] = [dict(predecessor_ref="absent", link_type="FS")]
    if bad == "cycle": body["items"][-1]["dependencies"] = [dict(predecessor_ref="new_work", link_type="FS")]
    if bad == "deleted_dependency":
        body["items"].pop(0); body["deleted_ids"] = [rows[0].id]
        body["items"][0]["dependencies"] = [dict(predecessor_id=rows[0].id, link_type="FS")]
    if bad == "duplicate_ref": body["items"].append(row_body())
    with pytest.raises(HTTPException) as caught:
        invoke(db_session, actor, baseline, body)
    assert caught.value.status_code == 422
    db_session.expire_all()
    assert baseline.graph_revision == 1 and baseline.planning_mode == "dates_only"
    assert db_session.scalar(select(func.count(ScheduleItem.id))) == 2
    assert db_session.scalar(select(func.count(AuditLog.id))) == 0


@pytest.mark.parametrize("protection", ["budget", "cash", "source_name", "source_excerpt", "actual_start", "actual_finish", "actual_progress", "status"])
def test_delete_protected_row_never_unlinks_or_rewrites(db_session, user_factory, protection):
    actor, baseline, rows = world(db_session, user_factory)
    row = rows[0]
    linked = None
    if protection == "budget":
        linked = BudgetLine(project_id=baseline.project_id, schedule_item_id=row.id,
                            category="Synthetic", description="Synthetic budget")
    elif protection == "cash":
        linked = CashFlowEntry(project_id=baseline.project_id, schedule_item_id=row.id,
                               direction="outflow", title="Synthetic cash", planned_date=date(2026,9,1), planned_amount=1)
    elif protection in {"source_name", "source_excerpt"}: setattr(row, protection, "Synthetic source")
    elif protection in {"actual_start", "actual_finish"}: setattr(row, protection, date(2026,9,1))
    elif protection == "actual_progress": row.actual_progress = 1
    else: row.status = "cancelled"
    if linked: db_session.add(linked)
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        invoke(db_session, actor, baseline, body_for(rows[1:], deleted_ids=[row.id]))
    assert caught.value.status_code == 409 and caught.value.detail == "schedule_row_delete_protected"
    assert baseline.graph_revision == 1 and db_session.get(ScheduleItem, row.id) is not None
    if linked: assert linked.schedule_item_id == row.id


def test_delete_and_rewire_complete_graph(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    api.put_schedule_graph(baseline.id, request(rows), db_session, actor)
    deleted = rows[0].id
    result = invoke(db_session, actor, baseline, body_for(rows[1:], expected_graph_revision=2, deleted_ids=[deleted]))
    assert result["graph_revision"] == 3 and result["client_ref_map"] == {}
    assert [row["id"] for row in result["items"]] == [rows[1].id]
    assert result["items"][0]["planned_start"] == date(2026,9,1)
    assert db_session.get(ScheduleItem, deleted) is None


@pytest.mark.parametrize("status", ["approved", "superseded", "cancelled"])
def test_immutable_baselines_reject_rows(db_session, user_factory, status):
    actor, baseline, rows = world(db_session, user_factory)
    baseline.status = status; db_session.commit()
    with pytest.raises(HTTPException) as caught: invoke(db_session, actor, baseline, body_for(rows))
    assert caught.value.status_code == 409 and caught.value.detail == "schedule_draft_required"


def test_viewer_cannot_change_rows(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    member = db_session.scalar(select(ProjectMember).where(ProjectMember.user_id == actor.id))
    member.role = "viewer"; db_session.commit()
    with pytest.raises(HTTPException) as caught: invoke(db_session, actor, baseline, body_for(rows))
    assert caught.value.status_code == 403


def test_outsider_denied_and_kept_row_facts_preserved(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    outsider = user_factory(); db_session.commit()
    with pytest.raises(HTTPException) as caught: invoke(db_session, outsider, baseline, body_for(rows))
    assert caught.value.status_code == 403
    rows[0].actual_progress = 25
    rows[0].source_excerpt = "Synthetic original provenance"
    db_session.commit()
    result = invoke(db_session, actor, baseline, body_for(rows))
    assert result["graph_revision"] == 2
    assert rows[0].actual_progress == 25 and rows[0].source_excerpt == "Synthetic original provenance"


@pytest.mark.parametrize("changes", [{"id":True}, {"id":1,"client_ref":"both"}, {"client_ref":"../path"},
                                      {"title":" "}, {"planned_start":"2026-01-01"}])
def test_strict_row_dto(changes):
    body = body_for([]); body["items"] = [row_body(**changes)]
    with pytest.raises(ValidationError): api.ScheduleGraphRowsPut.model_validate(body)


def test_late_audit_failure_rolls_back_insert_delete_and_revision(db_session, user_factory, monkeypatch):
    actor, baseline, rows = world(db_session, user_factory)
    old_id = rows[0].id
    body = body_for(rows[1:], deleted_ids=[old_id]); body["items"].append(row_body())
    def fail(*args, **kwargs): raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(api, "_audit", fail)
    with pytest.raises(RuntimeError): invoke(db_session, actor, baseline, body)
    db_session.expire_all()
    assert baseline.graph_revision == 1
    assert db_session.scalar(select(func.count(ScheduleItem.id))) == 2
    assert db_session.get(ScheduleItem, old_id) is not None


def test_clone_can_add_without_changing_approved_original(db_session, user_factory):
    from app.models.execution_finance import ScheduleBaseline
    actor, original, rows = world(db_session, user_factory)
    api.put_schedule_graph(original.id, request(rows), db_session, actor)
    api.update_status("baselines", original.id, api.StatusUpdate(status="approved",
        expected_status="draft", expected_graph_revision=2), db_session, actor)
    before = api.get_schedule_graph(original.id, db_session, actor)
    cloned = api.clone_baseline(original.id, api.BaselineClone(expected_version=1), db_session, actor)
    draft = db_session.get(ScheduleBaseline, cloned["id"])
    children = list(db_session.scalars(select(ScheduleItem).where(ScheduleItem.baseline_id == draft.id)))
    body = body_for(children, expected_graph_revision=draft.graph_revision)
    body["items"].append(row_body())
    assert len(invoke(db_session, actor, draft, body)["items"]) == 3
    assert api.get_schedule_graph(original.id, db_session, actor) == before


def test_empty_graph_and_new_milestone(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    emptied = invoke(db_session, actor, baseline, body_for([], deleted_ids=[row.id for row in rows]))
    assert emptied["items"] == [] and emptied["plan"]["project_finish"] is None
    body = body_for([], expected_graph_revision=2)
    body["items"] = [row_body(duration_days=0, is_milestone=True)]
    result = invoke(db_session, actor, baseline, body)
    assert result["items"][0]["planned_start"] == result["items"][0]["planned_finish"]


def test_pending_financial_unlink_cannot_bypass_guard(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    linked = BudgetLine(project_id=baseline.project_id, schedule_item_id=rows[0].id,
                        category="Synthetic", description="Synthetic budget")
    db_session.add(linked); db_session.commit()
    body = body_for(rows[1:], deleted_ids=[rows[0].id])
    _ = baseline.id, actor.id
    linked.schedule_item_id = None
    with pytest.raises(HTTPException) as caught:
        invoke(db_session, actor, baseline, body)
    assert caught.value.detail == "schedule_pending_changes"
    assert linked in db_session.dirty  # caller's transaction is neither flushed nor discarded
    db_session.rollback()
    assert linked.schedule_item_id == rows[0].id


def test_actual_http_rows_route_round_trip_and_replay():
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
                actor = User(name="Synthetic manager", email="rows@example.test", is_admin=False)
                db.add(actor); db.flush(); return actor
            actor, baseline, rows = world(db, factory)
            application = FastAPI(); application.include_router(api.router)
            application.dependency_overrides[api.get_db] = lambda: db
            application.dependency_overrides[api.require_user] = lambda: actor
            with TestClient(application) as client:
                path = f"/execution/baselines/{baseline.id}/graph"
                body = body_for(rows); body["items"].append(row_body())
                response = client.put(path + "/rows", json=body)
                assert response.status_code == 200
                result = response.json(); assert result.pop("client_ref_map")["new_work"] > 0
                assert client.get(path).json() == result
                assert client.put(path + "/rows", json=body).status_code == 409
                assert len(client.get(path).json()["items"]) == 3
    finally:
        engine.dispose()
