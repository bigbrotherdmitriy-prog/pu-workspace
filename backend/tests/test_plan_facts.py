from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Organization
from app.models.project import Project
from app.money_calendar import money_calendar


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="PF org")
        db.add(org)
        db.flush()
        project = Project(name="PF project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.commit()
        yield {"db": db, "project": project, "org": org}
    engine.dispose()


def _add_plan(db, project_id, **kwargs):
    values = {
        "direction": "inflow",
        "title": "Этап 1",
        "planned_date": date(2026, 5, 15),
        "actual_date": None,
        "planned_amount": Decimal("100.00"),
        "actual_amount": Decimal("0.00"),
        "invoiced_amount": Decimal("0.00"),
        "currency": "RUB",
        "status": "approved",
        "entry_kind": "plan_forecast",
    }
    values.update(kwargs)
    entry = CashFlowEntry(project_id=project_id, **values)
    db.add(entry)
    db.flush()
    return entry


def _record_fact(db, project, plan_id, *, amount, actual_date):
    from app.api.budget_from_dds import RecordFactRequest, record_plan_fact

    class _U:
        id = 1

    return record_plan_fact(
        project.id,
        plan_id,
        RecordFactRequest(amount=Decimal(amount), actual_date=actual_date),
        db=db,
        user=_U(),
    )


def test_partial_and_full_facts_by_plan_line(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    plan = _add_plan(db, project.id, planned_amount=Decimal("100.00"))
    db.commit()

    from app.core import auth as auth_module

    monkeypatch.setattr(auth_module, "require_project_role", lambda *a, **k: None)
    # API uses require_project_role imported at module load; patch it there too.
    import app.api.budget_from_dds as api_module

    monkeypatch.setattr(api_module, "require_project_role", lambda *a, **k: None)

    today = date(2026, 5, 10)
    resp = _record_fact(db, project, plan.id, amount="40.00", actual_date=today)
    assert resp["plan_remaining"] == "60.00"

    db.refresh(plan)
    assert plan.planned_amount == Decimal("100.00")
    assert plan.actual_amount == Decimal("0.00")

    fact = db.get(CashFlowEntry, resp["id"])
    assert fact is not None
    assert fact.status == "received"
    assert fact.actual_amount == Decimal("40.00")
    assert fact.plan_entry_id == plan.id
    assert fact.planned_amount == Decimal("0.00")

    resp2 = _record_fact(db, project, plan.id, amount="60.00", actual_date=today)
    assert resp2["plan_remaining"] == "0.00"

    with pytest.raises(Exception) as exc:
        _record_fact(db, project, plan.id, amount="1.00", actual_date=today)
    assert "остатка" in str(exc.value) or "422" in str(exc.value)


def test_plan_with_zero_amount_rejected(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    plan = _add_plan(db, project.id, planned_amount=Decimal("0.00"))
    db.commit()

    import app.api.budget_from_dds as api_module

    monkeypatch.setattr(api_module, "require_project_role", lambda *a, **k: None)

    with pytest.raises(Exception):
        _record_fact(db, project, plan.id, amount="10.00", actual_date=date(2026, 5, 10))


def test_money_calendar_inflow_full_fact_hidden_and_partial_reduced(world):
    db = world["db"]
    project = world["project"]
    today = date(2026, 5, 29)

    full = _add_plan(
        db, project.id,
        title="Полный",
        planned_date=today + timedelta(days=10),
        planned_amount=Decimal("100.00"),
    )
    partial = _add_plan(
        db, project.id,
        title="Частичный",
        planned_date=today + timedelta(days=10),
        planned_amount=Decimal("100.00"),
    )
    db.flush()
    db.add(CashFlowEntry(
        project_id=project.id, direction="inflow", title="Факт полный",
        planned_date=today, planned_amount=Decimal("0"), actual_amount=Decimal("100.00"),
        actual_date=today, invoiced_amount=Decimal("0"), currency="RUB",
        status="received", plan_entry_id=full.id, entry_kind="fact",
    ))
    db.add(CashFlowEntry(
        project_id=project.id, direction="inflow", title="Факт частичный",
        planned_date=today, planned_amount=Decimal("0"), actual_amount=Decimal("40.00"),
        actual_date=today, invoiced_amount=Decimal("0"), currency="RUB",
        status="received", plan_entry_id=partial.id, entry_kind="fact",
    ))
    db.commit()

    result = money_calendar(db, project.id, today=today)
    kinds_titles = [(i["kind"], i["title"]) for i in result["items"]]
    assert ("receive", "Полный") not in kinds_titles
    item = next(i for i in result["items"] if i["kind"] == "receive" and i["title"] == "Частичный")
    assert item["amount"] == "60.00"
    assert result["unmarked"]["count"] == 0
