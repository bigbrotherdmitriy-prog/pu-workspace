from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.core.auth import require_user
from app.database import Base, get_db
from app.main import app
from app.models.execution_finance import CashFlowEntry, CostCategory
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Budget org")
        user = User(name="Viewer", email="budget-viewer@example.test", is_admin=False)
        outsider = User(name="Outsider", email="budget-outsider@example.test", is_admin=False)
        db.add_all([org, user, outsider])
        db.flush()
        project = Project(name="Budget project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="viewer"))

        cat_a = CostCategory(
            organization_id=org.id,
            name="Материалы",
            normalized_name="материалы",
        )
        cat_b = CostCategory(
            organization_id=org.id,
            name="Логистика",
            normalized_name="логистика",
        )
        db.add_all([cat_a, cat_b])
        db.commit()

        old_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_user] = lambda: user
        try:
            client = TestClient(app)
            yield {
                "db": db,
                "client": client,
                "project": project,
                "user": user,
                "outsider": outsider,
                "cat_a": cat_a,
                "cat_b": cat_b,
            }
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old_overrides)
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


def test_budget_from_dds_aggregates_plan_fact_and_months(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]
    cat_a = world["cat_a"]
    cat_b = world["cat_b"]

    _add_entry(
        db,
        project.id,
        direction="inflow",
        title="Поступление",
        planned_date=date(2026, 1, 10),
        actual_date=date(2026, 1, 12),
        planned_amount=Decimal("1000.00"),
        actual_amount=Decimal("800.00"),
        status="approved",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Материалы 1",
        planned_date=date(2026, 1, 15),
        actual_date=date(2026, 1, 20),
        planned_amount=Decimal("300.00"),
        actual_amount=Decimal("250.00"),
        cost_category_id=cat_a.id,
        category="fallback-a",
        counterparty="ООО Ромашка",
        status="approved",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Материалы 2",
        planned_date=date(2026, 2, 5),
        actual_date=date(2026, 2, 10),
        planned_amount=Decimal("200.00"),
        actual_amount=Decimal("180.00"),
        cost_category_id=cat_a.id,
        counterparty="ООО Лютик",
        status="paid",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Логистика 1",
        planned_date=date(2026, 2, 20),
        actual_date=date(2026, 2, 25),
        planned_amount=Decimal("150.00"),
        actual_amount=Decimal("150.00"),
        cost_category_id=cat_b.id,
        counterparty="ООО Ромашка",
        status="approved",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Отменённый",
        planned_date=date(2026, 1, 1),
        actual_date=date(2026, 1, 1),
        planned_amount=Decimal("999.00"),
        actual_amount=Decimal("999.00"),
        status="cancelled",
    )
    db.commit()

    response = client.get(f"/projects/{project.id}/budget-from-dds")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["currency"] == "RUB"
    assert payload["income"] == {"plan": "1000.00", "fact": "800.00"}
    assert payload["expense_total"] == {"plan": "650.00", "fact": "580.00"}
    assert payload["profit"] == {"plan": "350.00", "fact": "220.00"}
    assert payload["margin_pct"]["plan"] == "35.00"
    assert payload["margin_pct"]["fact"] == "27.50"
    assert payload["other_currency_entries_count"] == 0

    by_category = {row["category"]: row for row in payload["expense_by_category"]}
    assert set(by_category) == {"Материалы", "Логистика"}
    materials = by_category["Материалы"]
    assert materials["category_id"] == cat_a.id
    assert materials["plan"] == "500.00"
    assert materials["fact"] == "430.00"
    assert materials["deviation"] == "-70.00"
    materials_cp = {cp["counterparty"]: cp for cp in materials["counterparties"]}
    assert materials_cp["ООО Ромашка"] == {
        "counterparty": "ООО Ромашка",
        "plan": "300.00",
        "fact": "250.00",
    }
    assert materials_cp["ООО Лютик"] == {
        "counterparty": "ООО Лютик",
        "plan": "200.00",
        "fact": "180.00",
    }
    logistics = by_category["Логистика"]
    assert logistics["category_id"] == cat_b.id
    assert logistics["plan"] == "150.00"
    assert logistics["fact"] == "150.00"
    assert logistics["deviation"] == "0.00"

    by_month = {row["month"]: row for row in payload["by_month"]}
    assert set(by_month) == {"2026-01", "2026-02"}

    jan = by_month["2026-01"]
    assert jan["income_plan"] == "1000.00"
    assert jan["income_fact"] == "800.00"
    assert jan["expense_plan"] == "300.00"
    assert jan["expense_fact"] == "250.00"
    assert jan["net_plan"] == "700.00"
    assert jan["net_fact"] == "550.00"
    assert jan["cumulative_plan"] == "700.00"
    assert jan["cumulative_fact"] == "550.00"

    feb = by_month["2026-02"]
    assert feb["income_plan"] == "0.00"
    assert feb["income_fact"] == "0.00"
    assert feb["expense_plan"] == "350.00"
    assert feb["expense_fact"] == "330.00"
    assert feb["net_plan"] == "-350.00"
    assert feb["net_fact"] == "-330.00"
    assert feb["cumulative_plan"] == "350.00"
    assert feb["cumulative_fact"] == "220.00"


def test_budget_from_dds_forbidden_without_role(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]
    outsider = world["outsider"]

    app.dependency_overrides[require_user] = lambda: outsider
    try:
        response = client.get(f"/projects/{project.id}/budget-from-dds")
        assert response.status_code == 403
    finally:
        app.dependency_overrides[require_user] = lambda: world["user"]
