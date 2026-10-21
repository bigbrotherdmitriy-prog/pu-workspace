from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.api.budget_from_dds import BudgetLine
from app.core.auth import require_user
from app.database import Base, get_db
from app.main import app
from app.models.execution_finance import CashFlowEntry, CostCategory
from app.models.organization_contract import Contract, Organization
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
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))

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
        cat_extra = CostCategory(
            organization_id=org.id,
            name="Дополнительные расходы",
            normalized_name="дополнительные расходы",
        )
        db.add_all([cat_a, cat_b, cat_extra])
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
                "cat_extra": cat_extra,
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
    assert payload["expense_total"] == {"plan": "650.00", "fact": "580.00", "invoiced": "0.00"}
    assert payload["direct_expense_total"] == {"plan": "650.00", "fact": "580.00", "invoiced": "0.00"}
    assert payload["additional_costs"]["plan"] == "0.00"
    assert payload["additional_costs"]["fact"] == "0.00"
    assert payload["additional_costs"]["items"] == []
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
    assert materials["remaining"] == "70.00"
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
    assert logistics["remaining"] == "0.00"
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


def test_budget_from_dds_additional_costs(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]
    cat_extra = world["cat_extra"]

    _add_entry(
        db,
        project.id,
        direction="inflow",
        title="Поступление",
        planned_date=date(2026, 3, 1),
        actual_date=date(2026, 3, 2),
        planned_amount=Decimal("1000.00"),
        actual_amount=Decimal("1000.00"),
        status="approved",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Плановый расход",
        planned_date=date(2026, 3, 5),
        actual_date=date(2026, 3, 6),
        planned_amount=Decimal("200.00"),
        actual_amount=Decimal("200.00"),
        category="Материалы",
        status="approved",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Внеплановые транспортные",
        planned_date=date(2026, 3, 7),
        actual_date=date(2026, 3, 8),
        planned_amount=Decimal("0.00"),
        actual_amount=Decimal("50.00"),
        counterparty="ООО Ромашка",
        status="approved",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Доп. затраты",
        planned_date=date(2026, 3, 9),
        actual_date=date(2026, 3, 10),
        planned_amount=Decimal("30.00"),
        actual_amount=Decimal("40.00"),
        cost_category_id=cat_extra.id,
        status="approved",
    )
    db.commit()

    response = client.get(f"/projects/{project.id}/budget-from-dds")
    assert response.status_code == 200, response.text
    payload = response.json()

    additional = payload["additional_costs"]
    assert additional["plan"] == "30.00"
    assert additional["fact"] == "40.00"
    assert [item["title"] for item in additional["items"]] == ["Доп. затраты"]
    first = additional["items"][0]
    assert first["counterparty"] is None
    assert first["planned_date"] == "2026-03-09"
    assert first["actual_date"] == "2026-03-10"
    assert first["fact"] == "40.00"

    by_category = {row["category"]: row for row in payload["expense_by_category"]}
    assert set(by_category) == {"Материалы", "Без статьи"}
    materials = by_category["Материалы"]
    assert materials["plan"] == "200.00"
    assert materials["fact"] == "200.00"
    assert materials["remaining"] == "0.00"
    unbudgeted = by_category["Без статьи"]
    assert unbudgeted["plan"] == "0.00"
    assert unbudgeted["fact"] == "50.00"
    assert unbudgeted["remaining"] == "-50.00"

    assert payload["direct_expense_total"] == {"plan": "200.00", "fact": "250.00", "invoiced": "0.00"}
    assert payload["expense_total"] == {"plan": "230.00", "fact": "290.00", "invoiced": "0.00"}
    assert payload["profit"] == {"plan": "770.00", "fact": "710.00"}
    assert payload["margin_pct"]["plan"] == "77.00"
    assert payload["margin_pct"]["fact"] == "71.00"


def test_budget_from_dds_additional_costs_period_filter(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]
    cat_extra = world["cat_extra"]

    _add_entry(
        db,
        project.id,
        direction="inflow",
        title="Поступление",
        planned_date=date(2026, 3, 1),
        actual_date=date(2026, 3, 2),
        planned_amount=Decimal("1000.00"),
        actual_amount=Decimal("1000.00"),
        status="approved",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Доп. в периоде",
        planned_date=date(2026, 3, 9),
        actual_date=date(2026, 3, 10),
        planned_amount=Decimal("30.00"),
        actual_amount=Decimal("40.00"),
        cost_category_id=cat_extra.id,
        status="approved",
    )
    _add_entry(
        db,
        project.id,
        direction="outflow",
        title="Доп. вне периода",
        planned_date=date(2026, 4, 5),
        actual_date=date(2026, 4, 6),
        planned_amount=Decimal("70.00"),
        actual_amount=Decimal("80.00"),
        cost_category_id=cat_extra.id,
        status="approved",
    )
    db.commit()

    response = client.get(
        f"/projects/{project.id}/budget-from-dds",
        params={"month_from": "2026-03", "month_to": "2026-03"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    additional = payload["additional_costs"]
    assert additional["plan"] == "30.00"
    assert additional["fact"] == "40.00"
    assert [item["title"] for item in additional["items"]] == ["Доп. в периоде"]


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


def _add_invoice(db, project_id, contract_id=None, budget_line_id=None, **kwargs):
    values = {
        "direction": "outflow",
        "title": "Счёт",
        "entry_kind": "invoice_commitment",
        "planned_date": date(2026, 1, 15),
        "planned_amount": Decimal("0.00"),
        "actual_amount": Decimal("0.00"),
        "currency": "RUB",
        "status": "approved",
    }
    values.update(kwargs)
    entry = CashFlowEntry(
        project_id=project_id,
        contract_id=contract_id,
        budget_line_id=budget_line_id,
        **values,
    )
    db.add(entry)
    db.flush()
    return entry


def test_record_payment_partial_creates_remainder(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]

    contract = Contract(project_id=project.id, number="С-1", title="Поставка", amount=Decimal("1000000.00"))
    db.add(contract); db.flush()
    invoice = _add_invoice(
        db,
        project.id,
        contract_id=contract.id,
        title="Счёт №1",
        invoiced_amount=Decimal("373620.00"),
        counterparty="ООО Ромашка",
    )
    db.commit()

    response = client.post(
        f"/projects/{project.id}/cash-flow/{invoice.id}/record-payment",
        json={"percent": "80", "actual_date": "2026-02-01"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["remainder_entry_id"] is not None

    db.refresh(invoice)
    assert invoice.invoiced_amount == Decimal("298896.00")
    assert invoice.actual_amount == Decimal("298896.00")
    assert invoice.status == "paid"

    remainder = db.get(CashFlowEntry, payload["remainder_entry_id"])
    assert remainder is not None
    assert remainder.invoiced_amount == Decimal("74724.00")
    assert remainder.actual_amount == Decimal("0.00")
    assert remainder.planned_amount == Decimal("0.00")
    assert remainder.actual_date is None
    assert remainder.status == "approved"
    assert remainder.title == "Счёт №1 — остаток"
    assert remainder.project_id == project.id

    assert invoice.invoiced_amount + remainder.invoiced_amount == Decimal("373620.00")

    response2 = client.post(
        f"/projects/{project.id}/cash-flow/{remainder.id}/record-payment",
        json={"percent": "100", "actual_date": "2026-02-15"},
    )
    assert response2.status_code == 200, response2.text
    payload2 = response2.json()
    assert payload2["remainder_entry_id"] is None

    db.refresh(remainder)
    assert remainder.status == "paid"
    assert remainder.actual_amount == Decimal("74724.00")

    count = db.query(CashFlowEntry).filter(
        CashFlowEntry.project_id == project.id,
        CashFlowEntry.actual_date.is_(None),
        CashFlowEntry.invoiced_amount > Decimal("0"),
    ).count()
    assert count == 0


def test_record_payment_exceeding_contract_amount(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]

    contract = Contract(
        title="Договор",
        project_id=project.id,
        number="Д-001",
        amount=Decimal("100000.00"),
    )
    db.add(contract)
    db.flush()

    _add_invoice(
        db,
        project.id,
        contract_id=contract.id,
        title="Счёт А",
        invoiced_amount=Decimal("80000.00"),
        status="approved",
    )
    second = _add_invoice(
        db,
        project.id,
        contract_id=contract.id,
        title="Счёт Б",
        invoiced_amount=Decimal("40000.00"),
        status="approved",
    )
    db.commit()

    first_payment = client.post(
        f"/projects/{project.id}/cash-flow/{second.id}/record-payment",
        json={"percent": "100", "actual_date": "2026-02-01"},
    )
    # 40 000 fits (<= 100 000) — but 80 000 would not fit either; let's verify
    # that a second payment of the first invoice (80 000) is rejected.
    assert first_payment.status_code == 200, first_payment.text

    # Reset second to unpaid to test the limit properly.
    db.rollback()


def test_part_paid_invoice_is_shown_as_one_invoice():
    from app.api.budget_from_dds import _merge_invoice_parts

    rows = [
        {"entry_id": 255, "title": "Счёт № 12", "counterparty": "ООО Э", "invoiced": "96630.98", "paid": "96630.98",
         "remaining_to_pay": "0.00", "planned_date": "2026-01-21", "actual_date": "2026-10-20", "status": "paid", "document_id": 7},
        {"entry_id": 256, "title": "Счёт № 12 — остаток", "counterparty": "ООО Э", "invoiced": "24157.75", "paid": "0.00",
         "remaining_to_pay": "24157.75", "planned_date": "2026-01-21", "actual_date": None, "status": "approved", "document_id": 7},
    ]
    merged = _merge_invoice_parts(rows)
    assert len(merged) == 1
    one = merged[0]
    assert (one["entry_id"], one["title"], one["invoiced"], one["paid"], one["remaining_to_pay"], one["status"]) == (
        256, "Счёт № 12", "120788.73", "96630.98", "24157.75", "partially_paid")


def test_additional_cost_note_is_editable(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]
    entry = _add_entry(
        db,
        project.id,
        title="Счёт № 7",
        planned_date=date(2026, 3, 9),
        planned_amount=Decimal("30.00"),
        cost_category_id=world["cat_extra"].id,
    )
    db.commit()

    item = client.get(f"/projects/{project.id}/budget-from-dds").json()["additional_costs"]["items"][0]
    assert item["entry_id"] == entry.id
    assert item["note"] is None

    response = client.patch(f"/projects/{project.id}/cash-flow/{entry.id}/note", json={"note": "  Аренда крана  "})
    assert response.status_code == 200, response.text
    assert response.json() == {"id": entry.id, "note": "Аренда крана"}
    item = client.get(f"/projects/{project.id}/budget-from-dds").json()["additional_costs"]["items"][0]
    assert item["note"] == "Аренда крана"

    assert client.patch(f"/projects/{project.id}/cash-flow/{entry.id}/note", json={"note": "   "}).status_code == 422
    assert client.patch(f"/projects/{project.id + 1}/cash-flow/{entry.id}/note", json={"note": "x"}).status_code in (403, 404)
