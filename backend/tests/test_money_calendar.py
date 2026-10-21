from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.contract_stage import ContractStage
from app.models.execution_finance import AcceptanceAct, CashFlowEntry
from app.models.organization_contract import Contract, Organization
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
        org = Organization(name="MC org")
        db.add(org)
        db.flush()
        project = Project(name="MC project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.commit()
        yield {"db": db, "project": project, "org": org}
    engine.dispose()


def _add_entry(db, project_id, **kwargs):
    values = {
        "direction": "outflow",
        "title": "Op",
        "planned_date": None,
        "actual_date": None,
        "planned_amount": Decimal("0.00"),
        "actual_amount": Decimal("0.00"),
        "invoiced_amount": Decimal("0.00"),
        "currency": "RUB",
        "status": "approved",
    }
    values.update(kwargs)
    entry = CashFlowEntry(project_id=project_id, **values)
    db.add(entry)
    db.flush()
    return entry


def test_pay_invoice_overdue(world):
    db = world["db"]
    project = world["project"]
    today = date(2026, 4, 15)
    _add_entry(
        db,
        project.id,
        title="Счёт №1",
        counterparty="ООО Ромашка",
        planned_date=today - timedelta(days=1),
        invoiced_amount=Decimal("1000.00"),
        actual_amount=Decimal("300.00"),
        status="approved",
    )
    db.commit()
    result = money_calendar(db, project.id, today=today)
    item = next(i for i in result["items"] if i["kind"] == "pay_invoice")
    assert item["amount"] == "700.00"
    assert item["level"] == "overdue"
    assert item["days_left"] == -1


def test_pay_plan_soon_and_reduced_by_linked_invoice(world):
    db = world["db"]
    project = world["project"]
    today = date(2026, 4, 15)
    plan = _add_entry(
        db,
        project.id,
        title="Плановая",
        direction="outflow",
        planned_date=today + timedelta(days=5),
        planned_amount=Decimal("1000.00"),
        status="approved",
    )
    db.flush()
    _add_entry(
        db,
        project.id,
        title="Счёт связанный",
        direction="outflow",
        planned_date=today - timedelta(days=1),
        invoiced_amount=Decimal("400.00"),
        plan_entry_id=plan.id,
        status="approved",
    )
    db.commit()
    result = money_calendar(db, project.id, today=today)
    item = next(i for i in result["items"] if i["kind"] == "pay_plan")
    assert item["level"] == "soon"
    assert item["amount"] == "600.00"


def test_sign_act_near_and_excluded_when_signed(world):
    db = world["db"]
    project = world["project"]
    org = world["org"]
    today = date(2026, 4, 15)
    contract = Contract(
        project_id=project.id,
        title="Договор 1",
        number="Д-1",
        counterparty="ООО Подряд",
    )
    db.add(contract)
    db.flush()
    stage = ContractStage(
        project_id=project.id,
        contract_id=contract.id,
        stage_number=1,
        title="Этап 1",
        amount=Decimal("500.00"),
        planned_finish=today + timedelta(days=10),
        status="planned",
    )
    db.add(stage)
    db.commit()

    result = money_calendar(db, project.id, today=today)
    stage_items = [i for i in result["items"] if i["kind"] == "sign_act"]
    assert len(stage_items) == 1
    assert stage_items[0]["level"] == "near"
    assert stage_items[0]["counterparty"] == "ООО Подряд"
    assert stage_items[0]["contract_number"] == "Д-1"

    act = AcceptanceAct(
        project_id=project.id,
        contract_id=contract.id,
        stage_id=stage.id,
        number="1",
        title="Акт этапа 1",
        status="signed",
    )
    db.add(act)
    db.commit()
    result = money_calendar(db, project.id, today=today)
    stage_items = [i for i in result["items"] if i["kind"] == "sign_act"]
    assert stage_items == []


def test_receive_ok(world):
    db = world["db"]
    project = world["project"]
    today = date(2026, 4, 15)
    _add_entry(
        db,
        project.id,
        title="Приход",
        direction="inflow",
        planned_date=today + timedelta(days=30),
        planned_amount=Decimal("2000.00"),
        status="approved",
    )
    db.commit()
    result = money_calendar(db, project.id, today=today)
    item = next(i for i in result["items"] if i["kind"] == "receive")
    assert item["level"] == "ok"
    assert item["amount"] == "2000.00"


def test_long_past_plan_rows_without_fact_are_one_unmarked_summary(world):
    db = world["db"]
    project = world["project"]
    today = date(2026, 10, 20)
    db.add(CashFlowEntry(project_id=project.id, direction="outflow", title="Старый план", planned_date=date(2026, 6, 30),
                         planned_amount=Decimal("1000"), actual_amount=Decimal("0"), currency="RUB", status="approved"))
    db.add(CashFlowEntry(project_id=project.id, direction="inflow", title="Старый приход", planned_date=date(2026, 7, 31),
                         planned_amount=Decimal("5000"), actual_amount=Decimal("0"), currency="RUB", status="approved"))
    db.commit()
    result = money_calendar(db, project.id, today=today)
    assert all(item["title"] not in ("Старый план", "Старый приход") for item in result["items"])
    assert result["unmarked"] == {"count": 2, "amount": "6000.00", "payments": 1, "receipts": 1}
