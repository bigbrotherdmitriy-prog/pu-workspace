"""Synthetic four-view DDS acceptance; no financial mutations by projection."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update

import app.api.execution_finance as api
from app.models.audit_log import AuditLog
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Contract
from app.models.project_member import ProjectMember
from app.models.user import User
from test_v7_schedule_graph_api import world


def entry(db, project, **changes):
    values = dict(project_id=project, title="Synthetic DDS", direction="outflow",
                  planned_date=date(2026,1,31), planned_amount=Decimal("100.10"),
                  actual_date=None, actual_amount=Decimal("0"), status="approved",
                  review_status="confirmed")
    values.update(changes)
    row = CashFlowEntry(**values); db.add(row); db.commit(); return row


def views(db, user, project, **changes):
    values = dict(project_id=project, date_from=date(2026,1,1), date_to=date(2026,2,28), db=db, user=user)
    values.update(changes)
    return api.cash_flow_views(**values)


def assert_consistent(result):
    for kind in ("planned", "actual"):
        for field in ("inflow", "outflow", "net"):
            target = Decimal(result["summary"][kind][field])
            assert sum((Decimal(row[kind][field]) for row in result["calendar"]), Decimal(0)) == target
            assert sum((Decimal(row[kind][field]) for row in result["months"]), Decimal(0)) == target
        relevant = [row for row in result["details"] if row["plan_in_period" if kind == "planned" else "actual_in_period"]]
        for direction in ("inflow", "outflow"):
            field = "planned_amount" if kind == "planned" else "actual_amount"
            assert sum((Decimal(row[field]) for row in relevant if row["direction"] == direction), Decimal(0)) == Decimal(result["summary"][kind][direction])


def test_four_views_share_rows_dates_and_exact_money(db_session, user_factory):
    actor, baseline, _ = world(db_session, user_factory)
    first = entry(db_session, baseline.project_id, status="paid", actual_date=date(2026,2,2), actual_amount=Decimal("90.05"))
    entry(db_session, baseline.project_id, direction="inflow", planned_amount=Decimal("1000.01"))
    before = db_session.scalar(select(func.count(AuditLog.id)))
    result = views(db_session, actor, baseline.project_id)
    assert result["currency"] == "RUB"
    assert result["summary"] == {"planned":{"inflow":"1000.01","outflow":"100.10","net":"899.91"},
                                  "actual":{"inflow":"0.00","outflow":"90.05","net":"-90.05"}}
    assert result["months"][0]["planned"]["outflow"] == "100.10"
    assert result["months"][1]["actual"]["outflow"] == "90.05"
    assert len(result["calendar"]) == 59
    assert next(day for day in result["calendar"] if day["date"] == "2026-02-02")["actual_entry_ids"] == [first.id]
    assert_consistent(result)
    assert db_session.scalar(select(func.count(AuditLog.id))) == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted


@pytest.mark.parametrize("status", ["proposed","cancelled","rejected","unexpected"])
def test_excluded_status_never_contributes_money(db_session, user_factory, status):
    actor, baseline, _ = world(db_session, user_factory)
    entry(db_session, baseline.project_id, status=status, actual_date=date(2026,2,2), actual_amount=999)
    result = views(db_session, actor, baseline.project_id)
    assert len(result["details"]) == 1
    assert not result["details"][0]["plan_in_period"] and not result["details"][0]["actual_in_period"]
    assert_consistent(result)
    assert result["summary"]["actual"]["outflow"] == "0.00"
    key = status if status in {"proposed","cancelled"} else "unsupported_status"
    assert result["excluded"][key] == 1


@pytest.mark.parametrize("changes", [{"actual_date":None}, {"actual_amount":Decimal("-1")}, {"actual_amount":Decimal("0")}, {"review_status":"required"}])
def test_invalid_actual_flagged_not_counted(db_session, user_factory, changes):
    actor, baseline, _ = world(db_session, user_factory)
    values = dict(status="paid", actual_date=date(2026,2,2), actual_amount=Decimal("70")); values.update(changes)
    entry(db_session, baseline.project_id, **values)
    result = views(db_session, actor, baseline.project_id)
    assert result["excluded"]["invalid_actual"] == 1
    expected_reasons = ["unconfirmed_plan", "invalid_actual"] if changes.get("review_status") == "required" else ["invalid_actual"]
    assert result["details"][0]["exclusion_reasons"] == expected_reasons
    assert result["summary"]["actual"]["outflow"] == "0.00"
    assert result["summary"]["planned"]["outflow"] == ("0.00" if changes.get("review_status") == "required" else "100.10")
    assert_consistent(result)


@pytest.mark.parametrize("status", ["approved", "paid", "received"])
@pytest.mark.parametrize("review", ["pending_confirmation", "required", "rejected"])
def test_unreviewed_active_rows_never_enter_confirmed_plan(db_session, user_factory, status, review):
    actor, baseline, _ = world(db_session, user_factory)
    row = entry(db_session, baseline.project_id, status=status, review_status=review,
                direction="inflow" if status == "received" else "outflow",
                actual_date=date(2026,2,2) if status != "approved" else None,
                actual_amount=Decimal("90.05") if status != "approved" else Decimal("0"))
    result = views(db_session, actor, baseline.project_id)
    assert result["excluded"]["unconfirmed_plan"] == 1
    assert "unconfirmed_plan" in result["details"][0]["exclusion_reasons"]
    assert not result["details"][0]["plan_in_period"]
    assert not result["details"][0]["actual_in_period"]
    assert_consistent(result)
    assert result["summary"]["planned"] == dict(inflow="0.00",outflow="0.00",net="0.00")
    db_session.refresh(row)
    assert row.review_status == review and row.status == status  # no implicit backfill


def test_period_uses_both_independent_dates_and_empty_leap_day(db_session, user_factory):
    actor, baseline, _ = world(db_session, user_factory)
    entry(db_session, baseline.project_id, status="paid", actual_date=date(2026,2,2), actual_amount=90)
    result = views(db_session, actor, baseline.project_id, date_from=date(2026,2,1))
    assert not result["details"][0]["plan_in_period"] and result["details"][0]["actual_in_period"]
    assert result["summary"]["planned"]["outflow"] == "0.00"
    assert_consistent(result)
    empty = views(db_session, actor, baseline.project_id, date_from=date(2024,2,29), date_to=date(2024,2,29))
    assert len(empty["calendar"]) == len(empty["months"]) == 1 and empty["details"] == []
    assert_consistent(empty)


def test_project_contract_and_stale_permissions(db_session, user_factory):
    actor, baseline, _ = world(db_session, user_factory)
    other_actor, other, _ = world(db_session, user_factory)
    own_contract = Contract(project_id=baseline.project_id, number="SYN-A", title="Synthetic A")
    foreign_contract = Contract(project_id=other.project_id, number="SYN-B", title="Synthetic B")
    db_session.add_all([own_contract,foreign_contract]); db_session.commit()
    entry(db_session, baseline.project_id, contract_id=own_contract.id)
    entry(db_session, baseline.project_id, planned_amount=888)
    entry(db_session, other.project_id, contract_id=foreign_contract.id, planned_amount=999)
    result = views(db_session, actor, baseline.project_id, contract_id=own_contract.id)
    assert len(result["details"]) == 1 and result["summary"]["planned"]["outflow"] == "100.10"
    with pytest.raises(HTTPException): views(db_session, actor, baseline.project_id, contract_id=foreign_contract.id)
    with pytest.raises(HTTPException) as caught: views(db_session, other_actor, baseline.project_id)
    assert caught.value.status_code == 403
    # Retained ORM actor must not preserve old admin rights.
    actor.is_admin = True; db_session.commit(); _ = actor.is_admin
    db_session.execute(update(User).where(User.id==actor.id).values(is_admin=False).execution_options(synchronize_session=False))
    db_session.execute(ProjectMember.__table__.delete().where(ProjectMember.user_id==actor.id))
    db_session.commit()
    with pytest.raises(HTTPException) as caught: views(db_session, actor, baseline.project_id)
    assert caught.value.status_code == 403


@pytest.mark.parametrize("start,end", [(date(2026,2,1),date(2026,1,1)),(date(2025,1,1),date(2026,1,2))])
def test_bounded_period(db_session, user_factory, start,end):
    actor, baseline, _ = world(db_session, user_factory)
    with pytest.raises(HTTPException) as caught: views(db_session,actor,baseline.project_id,date_from=start,date_to=end)
    assert caught.value.status_code == 422


def test_large_amount_cents_and_row_limit_fail_closed(db_session,user_factory,monkeypatch):
    actor, baseline, _ = world(db_session,user_factory)
    entry(db_session,baseline.project_id,planned_amount=Decimal("999999999999.99"))
    entry(db_session,baseline.project_id,planned_amount=Decimal("0.01"))
    result = views(db_session,actor,baseline.project_id)
    assert result["summary"]["planned"]["outflow"] == "1000000000000.00"
    monkeypatch.setattr(api,"CASH_FLOW_VIEW_ROW_LIMIT",1)
    with pytest.raises(HTTPException) as caught: views(db_session,actor,baseline.project_id)
    assert caught.value.status_code == 413


@pytest.mark.parametrize("direction", ["outflow", "inflow"])
def test_real_confirmation_correction_cancel_keeps_all_views_consistent(db_session,user_factory,direction):
    from test_mvp4_budget_dds import _chain
    from app.models.execution_finance import CashFlowFactHistory
    actor = user_factory()
    project,contract,stage,task,budget,document,version = _chain(db_session,actor)
    db_session.add(ProjectMember(project_id=project.id,user_id=actor.id,role="manager")); db_session.commit()
    created = api.create_cash_flow(api.CashFlowCreate(project_id=project.id,contract_id=contract.id,
        schedule_item_id=stage.id,task_id=task.id,budget_line_id=budget.id,source_document_id=document.id,
        direction=direction,title="Synthetic lifecycle",planned_date=date(2026,1,31),planned_amount="100.10",confidence="0.62"),db_session,actor)
    row = db_session.get(CashFlowEntry,created["id"])
    assert row.review_status == "required"
    with pytest.raises(HTTPException):
        api.confirm_payment(row.id,api.PaymentConfirmation(expected_record_version=row.record_version,actual_amount="90.05",actual_date=date(2026,2,2)),db_session,actor)
    assert views(db_session,actor,project.id)["summary"]["actual"][direction] == "0.00"
    api.update_status("cash-flow",row.id,api.StatusUpdate(status="approved",expected_status="proposed"),db_session,actor)
    payment = api.PaymentConfirmation(expected_record_version=row.record_version,actual_amount="90.05",actual_date=date(2026,2,2))
    api.confirm_payment(row.id,payment,db_session,actor)
    before = views(db_session,actor,project.id); assert_consistent(before)
    assert before["months"][1]["actual"][direction] == "90.05"
    assert api.confirm_payment(row.id,payment,db_session,actor)["already_confirmed"]
    assert views(db_session,actor,project.id) == before
    api.correct_payment(row.id,api.PaymentCorrection(expected_record_version=row.record_version,
        expected_actual_amount="90.05",expected_actual_date=date(2026,2,2),actual_amount="80.01",actual_date=date(2026,1,30),reason="Synthetic correction"),db_session,actor)
    corrected = views(db_session,actor,project.id); assert_consistent(corrected)
    assert corrected["months"][0]["actual"][direction] == "80.01"
    assert corrected["months"][1]["actual"][direction] == "0.00"
    assert corrected["months"][0]["planned"][direction] == "100.10"
    if direction == "outflow": assert budget.actual_amount == Decimal("80.01")
    api.update_status("cash-flow",row.id,api.StatusUpdate(status="cancelled",expected_status=row.status),db_session,actor)
    cancelled = views(db_session,actor,project.id); assert_consistent(cancelled)
    assert cancelled["summary"]["actual"][direction] == cancelled["summary"]["planned"][direction] == "0.00"
    assert cancelled["excluded"]["cancelled"] == 1
    if direction == "outflow": assert budget.actual_amount == Decimal("0")
    assert db_session.scalar(select(func.count(CashFlowFactHistory.id))) == 2


def test_only_one_ledger_select_and_no_writes(db_session,user_factory,monkeypatch):
    from sqlalchemy import event
    actor, baseline, _ = world(db_session,user_factory)
    entry(db_session,baseline.project_id)
    actor_id, project_id = actor.id, baseline.project_id
    selects = []
    def observe(conn,cursor,statement,parameters,context,executemany):
        if "FROM cash_flow_entries" in statement: selects.append(statement)
    def forbidden(*args,**kwargs): raise AssertionError("read view attempted mutation")
    monkeypatch.setattr(db_session,"commit",forbidden)
    monkeypatch.setattr(db_session,"flush",forbidden)
    event.listen(db_session.get_bind(),"before_cursor_execute",observe)
    try:
        result = views(db_session,actor,project_id)
        assert_consistent(result)
        assert len(selects) == 1 and actor.id == actor_id
    finally:
        event.remove(db_session.get_bind(),"before_cursor_execute",observe)


def test_pure_projection_rejects_duplicates_scope_and_malformed_money():
    from app.mvp4.cash_flow_views import project_cash_flow_views
    kwargs = dict(project_id=1,contract_id=None,date_from=date(2026,1,1),date_to=date(2026,1,31))
    row = dict(id=1,project_id=1,contract_id=None,status="paid",review_status="confirmed",direction="outflow",
               planned_date=date(2026,1,1),actual_date=date(2026,1,2),planned_amount=Decimal("1.01"),actual_amount=Decimal("NaN"))
    assert project_cash_flow_views([row],**kwargs)["excluded"]["invalid_actual"] == 1
    for amount in (float("inf"),Decimal("1.001"),None,True):
        row["actual_amount"] = amount
        assert project_cash_flow_views([row],**kwargs)["excluded"]["invalid_actual"] == 1
    with pytest.raises(ValueError): project_cash_flow_views([row,row],**kwargs)
    row["project_id"] = 2
    with pytest.raises(ValueError): project_cash_flow_views([row],**kwargs)


def test_archived_project_and_pending_changes_denied(db_session,user_factory):
    from datetime import datetime,timezone
    from app.models.project import Project
    actor, baseline, _ = world(db_session,user_factory)
    project = db_session.get(Project,baseline.project_id)
    _ = actor.id
    project.name = "Unsaved change"
    with pytest.raises(HTTPException) as caught: views(db_session,actor,baseline.project_id)
    assert caught.value.detail == "cash_flow_view_pending_changes"
    assert project in db_session.dirty
    db_session.rollback()
    project.archived_at = datetime.now(timezone.utc); db_session.commit()
    with pytest.raises(HTTPException) as caught: views(db_session,actor,baseline.project_id)
    assert caught.value.status_code == 403


def test_http_contract_strings_query_validation_and_date_extremes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    from app.mvp4.cash_flow_views import project_cash_flow_views
    engine = create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            def factory():
                actor=User(name="Synthetic reader",email="dds-reader@example.test",is_admin=False)
                db.add(actor);db.flush();return actor
            actor,baseline,_=world(db,factory)
            entry(db,baseline.project_id)
            app=FastAPI();app.include_router(api.router)
            app.dependency_overrides[api.get_db]=lambda:db
            app.dependency_overrides[api.require_user]=lambda:actor
            with TestClient(app) as client:
                params=dict(project_id=baseline.project_id,date_from="2026-01-01",date_to="2026-02-28")
                result=client.get("/execution/cash-flow/views",params=params)
                assert result.status_code==200
                body=result.json();assert_consistent(body)
                assert body["details"][0]["planned_amount"]=="100.10"
                assert body["external_effects"]==dict(payment_created=False,posting_created=False,automatic_conversion=False)
                assert client.get("/execution/cash-flow/views",params={**params,"project_id":0}).status_code==422
                assert client.get("/execution/cash-flow/views",params={**params,"contract_id":0}).status_code==422
                assert client.get("/execution/cash-flow/views",params={**params,"date_to":"invalid"}).status_code==422
    finally:
        engine.dispose()
    for day in (date.min,date.max):
        result=project_cash_flow_views([],project_id=1,contract_id=None,date_from=day,date_to=day)
        assert result["calendar"][0]["date"]==day.isoformat()
        assert result["months"][0]["month"]==day.isoformat()[:7]
