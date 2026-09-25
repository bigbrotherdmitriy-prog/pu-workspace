from __future__ import annotations

import hashlib
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register all mapped tables
from app.core.auth import require_user
from app.database import Base, get_db
from app.main import app
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import (
    AcceptanceAct,
    BudgetLine,
    CashFlowEntry,
    CashFlowPlanMutation,
    ContractBudgetProposal,
    InvoiceExtractionProposal,
    PaymentEvent,
    ProcurementItem,
    ScheduleBaseline,
    ScheduleItem,
)
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.user import User


@pytest.fixture
def finance_http_world():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)

    organization = Organization(name="V6-00a HTTP organization")
    owner = User(name="V6-00a owner", email="v6-money-owner@example.test", is_admin=True)
    session.add_all([organization, owner])
    session.flush()
    project = Project(name="V6-00a HTTP project", organization_id=organization.id, currency="RUB")
    session.add(project)
    session.flush()
    contract = Contract(project_id=project.id, number="V6-00a", title="Money boundary")
    session.add(contract)
    session.flush()
    baseline = ScheduleBaseline(
        project_id=project.id,
        contract_id=contract.id,
        created_by_user_id=owner.id,
        name="V6-00a baseline",
        version=1,
        status="draft",
    )
    session.add(baseline)
    session.flush()
    schedule_item = ScheduleItem(
        project_id=project.id,
        baseline_id=baseline.id,
        title="V6-00a schedule item",
        sort_order=1,
    )
    session.add(schedule_item)

    structured_content = (
        "Назначение платежа,Дата платежа,Сумма,Статья\n"
        "V6-00a imported row,2026-09-25,1.00,Прочее\n"
    )
    document = Document(
        project_id=project.id,
        name="v6-00a-money.csv",
        mime_type="text/csv",
        source="local_upload",
        status="processed",
        current_version=1,
    )
    session.add(document)
    session.flush()
    document_version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        content=structured_content,
    )
    session.add(document_version)
    session.flush()
    digest = hashlib.sha256(structured_content.encode("utf-8")).hexdigest()
    extraction = InvoiceExtractionProposal(
        project_id=project.id,
        source_document_id=document.id,
        source_document_version_id=document_version.id,
        source_document_sha256=digest,
        amount=Decimal("1.00"),
        currency="RUB",
        payment_purpose="V6-00a extraction",
        planned_date=None,
        confidence=Decimal("0.95"),
        extraction_method="regex",
        target_kind="cash_flow",
        status="proposed",
    )
    session.add(extraction)
    session.commit()

    def override_db() -> Iterator[Session]:
        yield session

    def override_user() -> User:
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_user] = override_user
    client = TestClient(app)
    try:
        yield {
            "client": client,
            "session": session,
            "project_id": project.id,
            "contract_id": contract.id,
            "schedule_item_id": schedule_item.id,
            "document_id": document.id,
            "document_version_id": document_version.id,
            "document_sha256": digest,
            "extraction_id": extraction.id,
        }
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_user, None)
        session.close()
        engine.dispose()


def _assert_2xx(response, endpoint: str) -> dict:
    assert 200 <= response.status_code < 300, f"{endpoint}: {response.status_code} {response.text}"
    return response.json()


def test_current_frontend_numeric_money_payloads_are_exact_across_financial_posts_and_patches(
    finance_http_world,
):
    client: TestClient = finance_http_world["client"]
    db: Session = finance_http_world["session"]
    project_id = finance_http_world["project_id"]
    contract_id = finance_http_world["contract_id"]
    schedule_item_id = finance_http_world["schedule_item_id"]

    budget = _assert_2xx(client.post("/execution/budget", json={
        "project_id": project_id,
        "contract_id": contract_id,
        "category": "Прочее",
        "description": "V6-00a budget",
        "planned_amount": 1500.5,
    }), "POST /execution/budget")
    budget_id = budget["id"]

    cash_flow = _assert_2xx(client.post("/execution/cash-flow", json={
        "project_id": project_id,
        "contract_id": contract_id,
        "direction": "outflow",
        "title": "V6-00a cash flow",
        "planned_date": "2026-09-25",
        "planned_amount": 1600.5,
    }), "POST /execution/cash-flow")
    cash_flow_id = cash_flow["id"]

    invoice = _assert_2xx(client.post("/execution/invoice-proposals", json={
        "project_id": project_id,
        "contract_id": contract_id,
        "schedule_item_id": schedule_item_id,
        "budget_line_id": budget_id,
        "direction": "outflow",
        "title": "V6-00a invoice",
        "planned_date": "2026-09-25",
        "planned_amount": 1700.5,
    }), "POST /execution/invoice-proposals")
    invoice_id = invoice["id"]

    procurement = _assert_2xx(client.post("/execution/procurement", json={
        "project_id": project_id,
        "contract_id": contract_id,
        "title": "V6-00a procurement",
        "planned_delivery": "2026-09-25",
        "planned_amount": 1800.5,
    }), "POST /execution/procurement")
    procurement_id = procurement["id"]

    act = _assert_2xx(client.post("/execution/acts", json={
        "project_id": project_id,
        "contract_id": contract_id,
        "budget_line_id": budget_id,
        "number": "V6-00a-act",
        "title": "V6-00a acceptance act",
        "act_date": "2026-09-25",
        "amount": 1900.5,
    }), "POST /execution/acts")
    act_id = act["id"]

    mutation = _assert_2xx(client.post(f"/execution/cash-flow/{cash_flow_id}/plan-mutations", json={
        "operation": "edit",
        "planned_date": "2026-09-26",
        "planned_amount": 2000.5,
        "expected_record_version": 1,
        "idempotency_key": "v6-00a-plan-mutation",
    }), "POST /execution/cash-flow/{id}/plan-mutations")

    confirmation = _assert_2xx(client.post(f"/execution/cash-flow/{invoice_id}/confirm-payment", json={
        "actual_amount": 2100.5,
        "actual_date": "2026-09-26",
        "idempotency_key": "v6-00a-confirm-payment",
    }), "POST /execution/cash-flow/{id}/confirm-payment")
    correction = _assert_2xx(client.post(f"/execution/cash-flow/{invoice_id}/correct-payment", json={
        "actual_amount": 2200.5,
        "actual_date": "2026-09-27",
        "reason": "V6-00a exact amount correction",
        "supersedes_event_id": confirmation["payment_event_id"],
        "idempotency_key": "v6-00a-correct-payment",
    }), "POST /execution/cash-flow/{id}/correct-payment")

    _assert_2xx(client.patch(f"/execution/procurement/{procurement_id}/status", json={
        "status": "ordered",
        "actual_amount": 2300.5,
        "actual_date": "2026-09-27",
    }), "PATCH /execution/{kind}/{id}/status")

    _assert_2xx(client.patch(
        f"/execution/invoice-extraction-proposals/{finance_http_world['extraction_id']}",
        json={"amount": 2400.5},
    ), "PATCH /execution/invoice-extraction-proposals/{id}")

    imported = _assert_2xx(client.post(
        f"/execution/documents/{finance_http_world['document_id']}/structured-import",
        json={
            "project_id": project_id,
            "contract_id": contract_id,
            "kind": "cash-flow",
            "direction": "outflow",
            "source_rows": [2],
            "row_overrides": {"2": {"amount": 2500.5}},
            "expected_document_version_id": finance_http_world["document_version_id"],
            "expected_document_sha256": finance_http_world["document_sha256"],
        },
    ), "POST /execution/documents/{id}/structured-import")

    contract = _assert_2xx(client.post(f"/projects/{project_id}/contracts", json={
        "number": "V6-00a-http",
        "title": "V6-00a frontend contract",
        "amount": 2600.5,
        "advance_amount": 100.5,
    }), "POST /projects/{project_id}/contracts")
    contract_id_from_api = contract["id"]
    updated_contract = _assert_2xx(client.patch(
        f"/projects/{project_id}/contracts/{contract_id_from_api}",
        json={
            "expected_record_version": contract["record_version"],
            "amount": 2700.5,
            "advance_amount": 200.5,
        },
    ), "PATCH /projects/{project_id}/contracts/{contract_id}")
    proposal = _assert_2xx(client.post(
        f"/projects/{project_id}/contracts/{contract_id_from_api}/budget-proposals",
    ), "POST /projects/{project_id}/contracts/{contract_id}/budget-proposals")
    edited_proposal = _assert_2xx(client.patch(
        f"/contract-budget-proposals/{proposal['id']}",
        json={"amount": 2800.5, "description": "V6-00a exact contract budget"},
    ), "PATCH /contract-budget-proposals/{proposal_id}")

    db.expire_all()
    assert db.get(BudgetLine, budget_id).planned_amount == Decimal("1500.50")
    assert db.get(CashFlowEntry, cash_flow_id).planned_amount == Decimal("2000.50")
    assert db.get(CashFlowPlanMutation, mutation["mutation_id"]).planned_amount == Decimal("2000.50")
    assert db.get(CashFlowEntry, invoice_id).planned_amount == Decimal("1700.50")
    assert db.get(CashFlowEntry, invoice_id).actual_amount == Decimal("2200.50")
    assert db.get(PaymentEvent, correction["payment_event_id"]).amount == Decimal("2200.50")
    assert db.get(ProcurementItem, procurement_id).planned_amount == Decimal("1800.50")
    assert db.get(ProcurementItem, procurement_id).actual_amount == Decimal("2300.50")
    assert db.get(AcceptanceAct, act_id).amount == Decimal("1900.50")
    assert db.get(InvoiceExtractionProposal, finance_http_world["extraction_id"]).amount == Decimal("2400.50")
    assert db.get(CashFlowEntry, imported["created_ids"][0]).planned_amount == Decimal("2500.50")
    stored_contract = db.get(Contract, contract_id_from_api)
    assert updated_contract["record_version"] == contract["record_version"] + 1
    assert stored_contract.amount == Decimal("2700.50")
    assert stored_contract.advance_amount == Decimal("200.50")
    assert edited_proposal["amount"] == 2800.5
    assert db.get(ContractBudgetProposal, proposal["id"]).amount == Decimal("2800.50")


@pytest.mark.parametrize("value", [1500.5, 1500.50, "1500.50", 1e3])
def test_financial_http_boundary_accepts_json_numbers_and_strings(finance_http_world, value):
    response = finance_http_world["client"].post("/execution/budget", json={
        "project_id": finance_http_world["project_id"],
        "category": "Прочее",
        "description": f"Boundary {value!s}",
        "planned_amount": value,
    })
    body = _assert_2xx(response, "POST /execution/budget")
    finance_http_world["session"].expire_all()
    assert finance_http_world["session"].get(BudgetLine, body["id"]).planned_amount == Decimal(str(value)).quantize(Decimal("0.01"))


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (0.1 + 0.2, "не более 2 знаков"),
        (12.345, "не более 2 знаков"),
        ("abc", "Некорректная денежная сумма"),
        ("NaN", "Денежная сумма должна быть конечной"),
        ("Infinity", "Денежная сумма должна быть конечной"),
        ("10000000000000000.00", "Денежная сумма превышает Numeric(18,2)"),
    ],
)
def test_financial_http_boundary_rejects_invalid_money_with_422(finance_http_world, value, message):
    response = finance_http_world["client"].post("/execution/budget", json={
        "project_id": finance_http_world["project_id"],
        "category": "Прочее",
        "description": "Invalid boundary",
        "planned_amount": value,
    })
    assert response.status_code == 422
    assert message in response.text
