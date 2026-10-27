from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.money_calendar import money_calendar
from app.receipts_by_basis import basis_kind, receipts_by_basis, record_receipt


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="RB org")
        db.add(org)
        db.flush()
        project = Project(name="RB project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.commit()
        yield {"db": db, "project": project, "org": org}
    engine.dispose()


def _add_document(db, project_id, **kwargs):
    values = {
        "direction": "inflow",
        "title": "Счёт на оплату № 3",
        "planned_date": date(2026, 5, 15),
        "actual_date": None,
        "planned_amount": Decimal("0.00"),
        "actual_amount": Decimal("0.00"),
        "invoiced_amount": Decimal("100.00"),
        "currency": "RUB",
        "status": "approved",
        "source_name": "uploaded_document",
    }
    values.update(kwargs)
    entry = CashFlowEntry(project_id=project_id, **values)
    db.add(entry)
    db.flush()
    return entry


def _add_customer_contract(db, project_id, number="Д-1", counterparty="Заказчик"):
    contract = Contract(
        project_id=project_id,
        title="Договор заказчика",
        number=number,
        counterparty=counterparty,
    )
    contract.contract_kind = "customer"
    db.add(contract)
    db.flush()
    return contract


def test_basis_kind_classifies_names():
    assert basis_kind("КС-3 № 5", None) == "КС-3"
    assert basis_kind("Акт № 17", None) == "Акт"
    assert basis_kind("Счёт на оплату № 3", None) == "Счёт"
    assert basis_kind("Справка о стоимости работ", None) == "КС-3"
    assert basis_kind("abc.pdf", None) == "Документ"


def test_receipts_flow(world):
    db = world["db"]
    project = world["project"]
    today = date(2026, 5, 10)

    _add_customer_contract(db, project.id)
    doc = _add_document(db, project.id, invoiced_amount=Decimal("100.00"))
    db.commit()

    result = receipts_by_basis(db, project.id, today)
    assert result["totals"]["invoiced"] == "100.00"
    assert result["totals"]["received"] == "0.00"
    assert result["totals"]["remaining"] == "100.00"
    assert len(result["contracts"]) == 1
    docs = result["contracts"][0]["documents"]
    assert len(docs) == 1
    assert docs[0]["kind"] == "Счёт"
    assert docs[0]["state"] == "expected"
    assert docs[0]["amount"] == "100.00"
    assert docs[0]["remaining"] == "100.00"

    resp = record_receipt(db, project.id, doc.id, Decimal("40.00"), today, user_id=1)
    db.commit()
    assert resp["remaining"] == "60.00"

    db.refresh(doc)
    assert doc.status != "received"

    result = receipts_by_basis(db, project.id, today)
    docs = result["contracts"][0]["documents"]
    assert docs[0]["received"] == "40.00"
    assert docs[0]["remaining"] == "60.00"
    assert docs[0]["state"] == "expected"

    resp2 = record_receipt(db, project.id, doc.id, Decimal("60.00"), today, user_id=1)
    db.commit()
    assert resp2["remaining"] == "0.00"

    db.refresh(doc)
    assert doc.status == "received"
    assert doc.actual_date == today

    result = receipts_by_basis(db, project.id, today)
    docs = result["contracts"][0]["documents"]
    assert docs[0]["state"] == "received"
    assert docs[0]["remaining"] == "0.00"

    with pytest.raises(HTTPException) as exc:
        record_receipt(db, project.id, doc.id, Decimal("1.00"), today, user_id=1)
    assert exc.value.status_code == 422


def test_overdue_state(world):
    db = world["db"]
    project = world["project"]
    today = date(2026, 5, 10)
    _add_customer_contract(db, project.id)
    _add_document(
        db,
        project.id,
        title="Акт № 17",
        planned_date=today - timedelta(days=3),
        invoiced_amount=Decimal("50.00"),
    )
    db.commit()

    result = receipts_by_basis(db, project.id, today)
    docs = result["contracts"][0]["documents"]
    assert len(docs) == 1
    assert docs[0]["state"] == "overdue"
    assert docs[0]["kind"] == "Акт"


def test_money_calendar_shows_unpaid_basis(world):
    db = world["db"]
    project = world["project"]
    today = date(2026, 5, 10)
    _add_customer_contract(db, project.id)
    doc = _add_document(
        db,
        project.id,
        title="Счёт на оплату № 3",
        planned_date=today + timedelta(days=5),
        invoiced_amount=Decimal("100.00"),
    )
    db.commit()

    result = money_calendar(db, project.id, today=today)
    receives = [i for i in result["items"] if i["kind"] == "receive"]
    assert len(receives) == 1
    assert receives[0]["title"] == "Счёт на оплату № 3"
    assert receives[0]["amount"] == "100.00"

    record_receipt(db, project.id, doc.id, Decimal("40.00"), today, user_id=1)
    db.commit()
    result = money_calendar(db, project.id, today=today)
    receives = [i for i in result["items"] if i["kind"] == "receive"]
    assert len(receives) == 1
    assert receives[0]["amount"] == "60.00"

    record_receipt(db, project.id, doc.id, Decimal("60.00"), today, user_id=1)
    db.commit()
    result = money_calendar(db, project.id, today=today)
    receives = [i for i in result["items"] if i["kind"] == "receive"]
    assert receives == []


def test_foreign_project_404(world):
    db = world["db"]
    project = world["project"]
    org = world["org"]
    other = Project(name="Other project", organization_id=org.id)
    db.add(other)
    db.flush()
    _add_customer_contract(db, project.id)
    doc = _add_document(db, project.id, invoiced_amount=Decimal("100.00"))
    db.commit()

    with pytest.raises(HTTPException) as exc:
        record_receipt(db, other.id, doc.id, Decimal("10.00"), date(2026, 5, 10), user_id=1)
    assert exc.value.status_code == 404
