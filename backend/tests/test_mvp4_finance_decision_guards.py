from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

import app.api.execution_finance as finance_api
from app.api.execution_finance import (
    BudgetCreate,
    CashFlowCreate,
    StatusUpdate,
    create_budget,
    create_cash_flow,
    overview,
    update_status,
)
from app.models.execution_finance import BudgetLine, CashFlowEntry
from app.models.organization_contract import Organization
from app.models.project import Project
from app.mvp4.finance_guards import finance_decision_requirements
from app.mvp4.supply.contracts import CreateDdsProposal, EvidenceLink, PrepareOrder


def _finance_world(db_session, user_factory):
    organization = Organization(name="Synthetic guarded finance tenant")
    user = user_factory(email="finance-guard@example.invalid")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic guarded finance project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    return user, project


def test_decision_contract_names_owner_and_legal_questions_without_answering_them():
    decisions = finance_decision_requirements(
        ["EUR"], has_implicit_currency_rows=True, has_financial_rows=True,
    )

    assert [item["code"] for item in decisions] == [
        "unknown_currency",
        "currency_conversion_policy",
        "exchange_rate_source",
        "mixed_currency",
        "vat_treatment",
        "retention_treatment",
    ]
    assert {item["decision_by"] for item in decisions} == {"OWNER", "LEGAL"}
    assert all("rate" not in item and "value" not in item for item in decisions)


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    [
        (BudgetCreate, "planned_amount", "1.001"),
        (CashFlowCreate, "planned_amount", "1.001"),
        (PrepareOrder, "ordered_quantity", "1.0001"),
        (CreateDdsProposal, "amount", "1.001"),
    ],
)
def test_money_and_quantity_reject_precision_that_storage_cannot_preserve(factory, field, value):
    evidence = EvidenceLink(
        evidence_id=UUID("00000000-0000-4000-8000-000000000001"),
        evidence_revision=1,
        source_version_id=UUID("00000000-0000-4000-8000-000000000002"),
        document_version_id=1,
    )
    values = {
        BudgetCreate: dict(project_id=1, category="synthetic", description="Synthetic budget", planned_amount="1"),
        CashFlowCreate: dict(
            project_id=1, direction="outflow", title="Synthetic cash flow",
            planned_date=date(2026, 9, 5), planned_amount="1",
        ),
        PrepareOrder: dict(
            command_key="synthetic-precision-order", expected_version=1,
            ordered_quantity="1", order_reference="SYN-ORDER-1",
        ),
        CreateDdsProposal: dict(
            command_key="synthetic-precision-dds", expected_version=1,
            contract_id=1, schedule_item_id=1, budget_line_id=1,
            planned_date=date(2026, 9, 5), amount="1", currency="RUB",
            evidence_assessment_version=1, evidence=evidence,
        ),
    }[factory]
    values[field] = value

    with pytest.raises(ValidationError):
        factory(**values)


def test_non_rub_budget_is_retained_for_review_but_cannot_be_approved_or_linked_to_rub_dds(
    db_session, user_factory, monkeypatch,
):
    user, project = _finance_world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    created = create_budget(
        BudgetCreate(
            project_id=project.id,
            category="Synthetic EUR",
            description="Synthetic foreign-currency proposal",
            planned_amount="125.50",
            currency="EUR",
        ),
        db_session,
        user,
    )
    budget = db_session.get(BudgetLine, created["id"])

    assert created == {
        "id": budget.id,
        "status": "proposed",
        "review_status": "required",
        "decision_required": ["unknown_currency", "currency_conversion_policy", "exchange_rate_source"],
        "automatic_conversion": False,
    }
    with pytest.raises(HTTPException) as approval_error:
        update_status(
            "budget", budget.id,
            StatusUpdate(status="approved", expected_status="proposed"),
            db_session, user,
        )
    assert approval_error.value.status_code == 409
    assert approval_error.value.detail["code"] == "decision_required"
    assert budget.status == "proposed"

    with pytest.raises(HTTPException) as link_error:
        create_cash_flow(
            CashFlowCreate(
                project_id=project.id,
                budget_line_id=budget.id,
                direction="outflow",
                title="Synthetic implicit-RUB DDS",
                planned_date=date(2026, 9, 6),
                planned_amount="125.50",
            ),
            db_session,
            user,
        )
    assert link_error.value.status_code == 409
    assert link_error.value.detail["code"] == "decision_required"
    assert db_session.scalar(select(CashFlowEntry).where(CashFlowEntry.project_id == project.id)) is None


def test_overview_excludes_foreign_currency_from_rub_totals_and_discloses_no_external_effects(
    db_session, user_factory, monkeypatch,
):
    user, project = _finance_world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    db_session.add_all([
        BudgetLine(
            project_id=project.id, category="RUB", description="Synthetic RUB",
            planned_amount=Decimal("100.00"), forecast_amount=Decimal("100.00"),
            currency="RUB", status="approved",
        ),
        BudgetLine(
            project_id=project.id, category="EUR", description="Synthetic EUR",
            planned_amount=Decimal("900.00"), forecast_amount=Decimal("900.00"),
            currency="EUR", status="approved",
        ),
    ])
    db_session.flush()

    result = overview(project.id, db_session, user)

    assert result["summary"]["budget_planned"] == Decimal("100.00")
    assert result["summary"]["excluded_currency_rows"] == 1
    assert result["summary"]["financial_totals_reliable"] is False
    assert {item["code"] for item in result["decision_requirements"]} >= {
        "unknown_currency", "mixed_currency", "vat_treatment", "retention_treatment",
    }
    assert result["external_effects"] == {
        "payment_created": False,
        "posting_created": False,
        "automatic_conversion": False,
    }
