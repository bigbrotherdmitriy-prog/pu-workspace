from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.cash_smoothing import MAX_COMBINED_MOVES, propose_smoothing
from app.database import Base
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Organization
from app.models.project import Project

TODAY = date(2026, 1, 1)


def _row(i, direction, amount, day, actual=0, status="planned"):
    return SimpleNamespace(
        id=i, direction=direction, title=f"e{i}", planned_amount=Decimal(amount),
        planned_date=TODAY + timedelta(days=day), actual_amount=Decimal(actual), status=status,
    )


def test_no_gap_gives_no_variants():
    res = propose_smoothing([_row(1, "inflow", 100, 2), _row(2, "outflow", 50, 5)], TODAY)
    assert res["ok"] is True and res["variants"] == []


def test_delay_payment_resolves_gap():
    rows = [_row(1, "outflow", 100, 5), _row(2, "inflow", 100, 10)]
    res = propose_smoothing(rows, TODAY)
    v = next(x for x in res["variants"] if x["key"] == "delay_payments")
    assert v["resolved"] and Decimal(v["min_balance"]) == 0
    assert v["moves"][0]["entry_id"] == 1 and v["moves"][0]["days"] >= 5
    assert Decimal(res["before"]["min_balance"]) == Decimal("-100")


def test_pull_receipt_within_limit():
    rows = [_row(1, "outflow", 100, 5), _row(2, "inflow", 100, 10)]
    res = propose_smoothing(rows, TODAY)
    v = next(x for x in res["variants"] if x["key"] == "pull_receipts")
    assert v["moves"][0]["entry_id"] == 2 and v["moves"][0]["days"] < 0
    assert abs(v["moves"][0]["days"]) <= 14 and v["resolved"]


def test_done_and_past_entries_do_not_move():
    rows = [
        _row(1, "outflow", 100, 5, actual=100),
        _row(2, "inflow", 100, 10),
        _row(3, "outflow", 10, -3),
    ]
    res = propose_smoothing(rows, TODAY)
    for v in res["variants"]:
        assert all(m["entry_id"] not in (1, 3) for m in v["moves"])


def test_limits_respected():
    rows = [_row(1, "outflow", 100, 5), _row(2, "inflow", 100, 100)]
    res = propose_smoothing(rows, TODAY)
    for v in res["variants"]:
        for m in v["moves"]:
            limit = 30 if m["direction"] == "outflow" else 14
            assert abs(m["days"]) <= limit
        assert not v["resolved"] or Decimal(v["min_balance"]) >= 0


def test_combined_move_cap():
    rows = [_row(i, "outflow", 10, 5 + i) for i in range(1, 30)] + [_row(100, "inflow", 1000, 40)]
    res = propose_smoothing(rows, TODAY)
    for v in res["variants"]:
        if v["key"] == "combined":
            assert v["moves_count"] <= MAX_COMBINED_MOVES


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Smooth org")
        db.add(org)
        db.flush()
        project = Project(name="P", organization_id=org.id)
        other = Project(name="O", organization_id=org.id)
        db.add_all([project, other])
        db.flush()
        yield {"db": db, "project": project, "other": other}
    engine.dispose()


def test_api_returns_variants_and_does_not_mutate(world, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from app.api import cash_smoothing as api
    from app.core.auth import require_user
    from app.database import get_db

    db, project, other = world["db"], world["project"], world["other"]
    today = date.today()
    db.add_all([
        CashFlowEntry(project_id=project.id, direction="outflow", title="pay", planned_date=today + timedelta(days=5), planned_amount=Decimal("100"), status="planned"),
        CashFlowEntry(project_id=project.id, direction="inflow", title="in", planned_date=today + timedelta(days=10), planned_amount=Decimal("100"), status="planned"),
    ])
    db.commit()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    client = TestClient(app)

    r = client.get(f"/projects/{project.id}/cash-flow/smoothing")
    assert r.status_code == 200 and r.json()["variants"]
    dates = sorted(e.planned_date for e in db.query(CashFlowEntry).all())
    assert dates == [today + timedelta(days=5), today + timedelta(days=10)]

    def scoped(_db, _user, pid, minimum="viewer"):
        if pid != project.id:
            raise HTTPException(status_code=404, detail="nf")
        return "viewer"

    monkeypatch.setattr(api, "require_project_role", scoped)
    assert client.get(f"/projects/{other.id}/cash-flow/smoothing").status_code == 404
