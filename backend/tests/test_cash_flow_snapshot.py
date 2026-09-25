from datetime import date
from decimal import Decimal
import copy

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.cash_flow_snapshot import SnapshotError, build_snapshot, read_snapshot
from app.core.auth import require_user
from app.database import Base, get_db
from app.main import app
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Organization, Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


def row(id=1, **kwargs):
    return {"id": id, "planned_date": date(2026, 1, 31), "actual_date": date(2026, 2, 1),
            "planned_amount": Decimal("1500.50"), "actual_amount": Decimal("1400.01"),
            "status": "paid", "direction": "outflow", "currency": "RUB",
            "title": "Snapshot test", "category": "Works", "object_name": "Site", **kwargs}


def snapshot(rows, **kwargs):
    return build_snapshot(**{"project_id": 1, "currency": "RUB", "revision": 1,
                             "date_from": date(2026, 1, 1), "date_to": date(2026, 3, 1),
                             "rows": rows, **kwargs})


def assert_invariants(value):
    for component in ("plan", "fact"):
        for direction in ("inflow", "outflow", "net"):
            expected = int(value["summary"]["totals"][component][direction]["amount_minor"])
            for rows in (value["months"], value["calendar"], value["details"],
                         value["summary"]["categories"], value["summary"]["objects"]):
                assert sum(int(r["totals"][component][direction]["amount_minor"]) for r in rows) == expected
        totals = value["summary"]["totals"][component]
        assert int(totals["net"]["amount_minor"]) == int(totals["inflow"]["amount_minor"]) - int(totals["outflow"]["amount_minor"])


def test_four_views_exact_large_money_and_month_boundaries():
    value = snapshot([row(), row(2, direction="inflow", status="received", category="Income",
                                object_name=None, planned_amount=Decimal("9999999999999999.99")),
                      row(3, status="approved"), row(4, status="proposed"), row(5, status="cancelled")],
                     include_review_rows=True, statuses=("approved", "paid", "received", "proposed", "cancelled"))
    assert_invariants(value)
    assert value["months"][0]["totals"]["fact"]["outflow"]["amount"] == "0.00"
    assert value["months"][1]["totals"]["fact"]["outflow"]["amount"] == "1400.01"
    assert value["months"][2]["totals"]["plan"]["outflow"]["amount"] == "0.00"
    assert len(value["calendar"]) == 60
    assert len(value["excluded"]) == 2


@pytest.mark.parametrize("period,plan,fact", [(date(2026, 1, 31), "1500.50", "0.00"), (date(2026, 2, 1), "0.00", "1400.01")])
def test_plan_fact_independent_inclusive_dates(period, plan, fact):
    value = snapshot([row()], date_from=period, date_to=period)
    assert_invariants(value)
    assert value["summary"]["totals"]["plan"]["outflow"]["amount"] == plan
    assert value["summary"]["totals"]["fact"]["outflow"]["amount"] == fact


@pytest.mark.parametrize("status", ["proposed", "cancelled"])
def test_review_status_never_counts_even_when_requested(status):
    value = snapshot([row(status=status)], statuses=(status,), include_review_rows=True)
    assert_invariants(value)
    assert len(value["details"]) == 1
    assert value["summary"]["totals"]["plan"]["outflow"]["amount"] == "0.00"
    assert snapshot([row(status=status)], statuses=(status,))["details"] == []


@pytest.mark.parametrize("change,code", [({"status": "unknown"}, "unsupported_status"),
    ({"currency": "USD"}, "currency_mismatch"), ({"direction": "inflow"}, "invalid_status_direction"),
    ({"status": "received"}, "invalid_status_direction"), ({"direction": "wrong"}, "invalid_status_direction"),
    ({"actual_date": None}, "invalid_actual"), ({"actual_amount": Decimal("0")}, "invalid_actual"),
    ({"planned_amount": Decimal("0.001")}, "invalid_money"), ({"actual_amount": Decimal("NaN")}, "invalid_money")])
def test_integrity_fail_closed_even_for_filtered_out_rows(change, code):
    with pytest.raises(SnapshotError) as caught:
        snapshot([row(**change)], statuses=("proposed",))
    assert caught.value.code == code


@pytest.mark.parametrize("statuses", [(), ("bad",), ("approved", "")])
def test_bad_status_filter(statuses):
    with pytest.raises(SnapshotError, match="статус"):
        snapshot([], statuses=statuses)


def test_period_limit_and_empty_snapshot():
    assert_invariants(snapshot([]))
    snapshot([], date_from=date(2024, 1, 1), date_to=date(2024, 12, 31))
    for end in (date(2023, 12, 31), date(2025, 1, 1)):
        with pytest.raises(SnapshotError, match="366"):
            snapshot([], date_from=date(2024, 1, 1), date_to=end)


def test_row_limit_rejects_without_partial_totals(monkeypatch):
    monkeypatch.setattr("app.cash_flow_snapshot.MAX_ROWS", 1)
    with pytest.raises(SnapshotError) as caught:
        snapshot([row(), row(2)])
    assert caught.value.status_code == 413


def test_hash_stable_canonical_and_sensitive_to_revision_scope_and_sources():
    rows = [row(), row(2)]
    original = copy.deepcopy(rows)
    value = snapshot(rows)
    assert value == snapshot(list(reversed(rows)))
    assert rows == original
    for changes in ({"revision": 2}, {"include_review_rows": True}, {"contract_id": 1},
                    {"statuses": ("paid",)}, {"date_to": date(2026, 2, 28)}):
        assert snapshot(rows, **changes)["snapshot_hash"] != value["snapshot_hash"]
    for field, changed in {"title": "Changed", "planned_amount": Decimal("1500.51"),
                           "category": "Other", "object_name": "Elsewhere"}.items():
        assert snapshot([row(**{field: changed}), row(2)])["snapshot_hash"] != value["snapshot_hash"]


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Snapshot org")
        user = User(name="Reader", email="snapshot@example.test", is_admin=False)
        db.add_all([org, user]); db.flush()
        project = Project(name="Snapshot project", organization_id=org.id)
        other = Project(name="Other", organization_id=org.id)
        db.add_all([project, other]); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="viewer"))
        contract = Contract(project_id=other.id, number="OTHER", title="Other contract")
        db.add(contract); db.commit()
        old_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_user] = lambda: user
        try:
            client = TestClient(app)
            yield db, client, project, other, contract
        finally:
            app.dependency_overrides.clear(); app.dependency_overrides.update(old_overrides)
    engine.dispose()


def test_http_scope_access_and_validation(world):
    db, client, project, other, contract = world
    params = {"project_id": project.id, "date_from": "2026-01-01", "date_to": "2026-03-01"}
    url = "/execution/cash-flow/views"
    assert client.get(url, params=params).status_code == 200
    assert client.get(url, params={**params, "project_id": other.id}).status_code == 403
    assert client.get(url, params={**params, "contract_id": contract.id}).status_code in (404, 422)
    assert client.get(url, params={**params, "statuses": "unknown"}).status_code == 422
    assert client.get(url, params={"project_id": project.id}).status_code == 422
    assert client.get(url, params={**params, "date_to": "2025-01-01"}).status_code == 422


def test_single_source_select_no_writes_and_period_filter(world):
    db, client, project, other, contract = world
    values = row(); values.pop("id")
    db.add_all([CashFlowEntry(project_id=project.id, **values),
                CashFlowEntry(project_id=other.id, **values),
                CashFlowEntry(project_id=project.id, **{**values, "planned_date": date(2025, 1, 1), "actual_date": None, "status": "approved"})])
    db.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        project_id = project.id
        statements.clear()
        value = read_snapshot(db, project_id=project_id, date_from=date(2026, 2, 1), date_to=date(2026, 2, 28))
        assert len(statements) == 1 and statements[0].lstrip().upper().startswith("SELECT")
        assert value["source_row_count"] == 1
        assert_invariants(value)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)


@pytest.mark.parametrize("change,code", [({"status": "unknown"}, "unsupported_status"),
                                        ({"currency": "USD"}, "currency_mismatch")])
def test_http_integrity_errors_have_no_partial_totals(world, change, code):
    db, client, project, _, _ = world
    values = row(**change); values.pop("id")
    db.add(CashFlowEntry(project_id=project.id, **values)); db.commit()
    response = client.get("/execution/cash-flow/views", params={"project_id": project.id,
                          "date_from": "2026-01-01", "date_to": "2026-03-01"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code
    assert "summary" not in response.json()


def test_database_limit_includes_excluded_rows(world, monkeypatch):
    db, client, project, _, _ = world
    values = row(status="proposed"); values.pop("id")
    db.add_all([CashFlowEntry(project_id=project.id, **values) for _ in range(3)]); db.commit()
    monkeypatch.setattr("app.cash_flow_snapshot.MAX_ROWS", 2)
    response = client.get("/execution/cash-flow/views", params={"project_id": project.id,
                          "date_from": "2026-01-01", "date_to": "2026-03-01"})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "row_limit_exceeded"


def test_many_rows_invariants_no_floats_and_stable_status_order():
    rows = [row(i, planned_amount=Decimal(i) / 100, actual_amount=Decimal(i+1) / 100,
                category=f"Category {i % 7}", object_name=f"Object {i % 4}",
                direction="inflow" if i % 2 else "outflow",
                status="received" if i % 2 else "paid") for i in range(1, 151)]
    value = snapshot(rows)
    assert_invariants(value)
    assert snapshot(rows, statuses=("received", "approved", "paid", "paid")) == value
    def check(item):
        assert not isinstance(item, float)
        if isinstance(item, dict):
            for child in item.values(): check(child)
        elif isinstance(item, list):
            for child in item: check(child)
    check(value)
