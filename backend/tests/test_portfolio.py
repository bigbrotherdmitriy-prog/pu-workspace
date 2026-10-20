from datetime import date
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
from app.models.project_member import ProjectMember
from app.models.user import User
from app.api import portfolio as api


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Portfolio org")
        user = User(name="Manager", email="portfolio-manager@example.test", is_admin=False)
        outsider = User(name="Outsider", email="portfolio-outsider@example.test", is_admin=False)
        db.add_all([org, user, outsider])
        db.flush()
        project = Project(name="Portfolio project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        db.commit()
        yield {
            "db": db,
            "org": org,
            "project": project,
            "user": user,
            "outsider": outsider,
        }
    engine.dispose()


def _add_entry(db, project_id, **kwargs):
    values = {
        "direction": "outflow",
        "title": "Op",
        "planned_date": date(2026, 1, 15),
        "actual_date": None,
        "planned_amount": Decimal("0.00"),
        "actual_amount": Decimal("0.00"),
        "currency": "RUB",
        "status": "approved",
    }
    values.update(kwargs)
    entry = CashFlowEntry(project_id=project_id, **values)
    db.add(entry)
    db.flush()
    return entry


def test_risk_project_with_cash_gap(world):
    db = world["db"]
    project = world["project"]
    user = world["user"]

    _add_entry(
        db, project.id,
        direction="outflow",
        title="Материалы",
        planned_date=date(2026, 1, 10),
        planned_amount=Decimal("1000.00"),
        actual_amount=Decimal("0.00"),
        actual_date=None,
        status="approved",
    )
    _add_entry(
        db, project.id,
        direction="inflow",
        title="Оплата",
        planned_date=date(2026, 3, 10),
        planned_amount=Decimal("500.00"),
        actual_amount=Decimal("0.00"),
        actual_date=None,
        status="approved",
    )
    db.commit()

    result = api.get_portfolio(db=db, user=user)
    items = result["projects"]
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "risk"
    assert item["cash_gap_month"] == "2026-01"
    assert items[0]["project_id"] == project.id


def test_overdue_outflow(world):
    db = world["db"]
    project = world["project"]
    user = world["user"]

    _add_entry(
        db, project.id,
        direction="outflow",
        title="Просрочка",
        planned_date=date(2020, 1, 10),
        planned_amount=Decimal("300.00"),
        invoiced_amount=Decimal("300.00"),
        actual_amount=Decimal("0.00"),
        actual_date=None,
        status="approved",
    )
    _add_entry(
        db, project.id,
        direction="outflow",
        title="План без счёта",
        planned_date=date(2020, 1, 10),
        planned_amount=Decimal("900.00"),
        actual_amount=Decimal("0.00"),
        actual_date=None,
        status="approved",
    )
    db.commit()

    result = api.get_portfolio(db=db, user=user)
    item = next(p for p in result["projects"] if p["project_id"] == project.id)
    assert item["overdue_count"] == 1
    assert item["overdue_amount"] == "300.00"


def test_outsider_does_not_see_project(world):
    db = world["db"]
    project = world["project"]
    outsider = world["outsider"]

    _add_entry(
        db, project.id,
        direction="outflow",
        title="X",
        planned_date=date(2026, 1, 10),
        planned_amount=Decimal("100.00"),
        actual_amount=Decimal("0.00"),
        actual_date=None,
        status="approved",
    )
    db.commit()

    result = api.get_portfolio(db=db, user=outsider)
    assert result["projects"] == []
    assert result["totals"]["projects"] == 0


def test_ok_project_without_entries(world):
    db = world["db"]
    user = world["user"]

    result = api.get_portfolio(db=db, user=user)
    items = result["projects"]
    assert len(items) == 1
    assert items[0]["status"] == "ok"
    assert items[0]["overdue_count"] == 0
    assert items[0]["cash_gap_month"] is None
