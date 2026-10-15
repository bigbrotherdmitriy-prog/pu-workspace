"""A confirmed invoice is fact ("выставлено") against a plan DDS line, never plan (owner, 15.10.2026)."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

import app.api.execution_finance as finance_api
from app.api.execution_finance import (
    InvoiceExtractionConfirm, InvoiceExtractionCreate, InvoiceExtractionUpdate,
    confirm_invoice_extraction, create_invoice_extraction, update_invoice_extraction,
)
from app.invoice_extraction import InvoiceFields
from app.models.execution_finance import CashFlowEntry, CostCategory
from app.models.organization_contract import Contract
from tests.test_invoice_cost_classification import _world


def _proposal(db_session, user, project, document, monkeypatch):
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_a, **_k: None)
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_a, **_k: InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="125 400,50 руб.", currency="RUB",
        counterparty="ООО Бетон", counterparty_evidence_quote="ООО Бетон",
        payment_purpose="строительные материалы", payment_purpose_evidence_quote="материалы",
        suggested_category_name="Тест Материалы", category_evidence_quote="материалы",
        planned_date=date(2026, 9, 19), confidence=0.92, extraction_method="llm",
    ))
    proposed = create_invoice_extraction(document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user)
    update_invoice_extraction(proposed["id"], InvoiceExtractionUpdate(
        amount="125400.50", counterparty="ООО Бетон", payment_purpose="Оплата материалов",
        planned_date="2026-09-20", target_kind="cash_flow",
    ), db_session, user)
    return proposed["id"]


def _plan_line(db_session, project):
    contract = Contract(project_id=project.id, number="PLAN-1", title="Plan contract", status="active")
    db_session.add(contract); db_session.flush()
    category = CostCategory(organization_id=project.organization_id, name="Тест СМР",
                            normalized_name="тест смр", is_active=True)
    db_session.add(category); db_session.flush()
    plan = CashFlowEntry(project_id=project.id, contract_id=contract.id, direction="outflow",
                         title="Дубна, монтаж, материалы", planned_date=date(2026, 9, 30),
                         planned_amount=Decimal("500000"), currency="RUB", counterparty="ООО Бетон",
                         cost_category_id=category.id, category=category.name, status="approved")
    db_session.add(plan); db_session.flush()
    return contract, category, plan


def test_invoice_on_plan_line_is_invoiced_fact_and_plan_stays_unchanged(db_session, user_factory, monkeypatch):
    user, project, document, _ = _world(db_session, user_factory)
    proposal_id = _proposal(db_session, user, project, document, monkeypatch)
    contract, category, plan = _plan_line(db_session, project)

    confirmed = confirm_invoice_extraction(proposal_id, InvoiceExtractionConfirm(plan_entry_id=plan.id), db_session, user)

    fact = db_session.get(CashFlowEntry, confirmed["created_cash_flow_id"])
    assert fact.planned_amount == Decimal("0")
    assert fact.invoiced_amount == Decimal("125400.50")
    assert fact.plan_entry_id == plan.id
    assert fact.contract_id == contract.id and fact.cost_category_id == category.id
    db_session.refresh(plan)
    assert plan.planned_amount == Decimal("500000") and plan.invoiced_amount == Decimal("0")


def test_invoice_as_additional_costs_uses_dedicated_category(db_session, user_factory, monkeypatch):
    user, project, document, _ = _world(db_session, user_factory)
    proposal_id = _proposal(db_session, user, project, document, monkeypatch)
    contract, _category, _plan = _plan_line(db_session, project)

    confirmed = confirm_invoice_extraction(
        proposal_id, InvoiceExtractionConfirm(additional=True, contract_id=contract.id), db_session, user)

    fact = db_session.get(CashFlowEntry, confirmed["created_cash_flow_id"])
    assert fact.category == "Дополнительные затраты"
    assert fact.planned_amount == Decimal("0") and fact.invoiced_amount == Decimal("125400.50")
    assert fact.plan_entry_id is None


def test_plan_line_and_additional_together_are_rejected(db_session, user_factory, monkeypatch):
    user, project, document, _ = _world(db_session, user_factory)
    proposal_id = _proposal(db_session, user, project, document, monkeypatch)
    _contract, _category, plan = _plan_line(db_session, project)
    with pytest.raises(HTTPException) as failure:
        confirm_invoice_extraction(proposal_id, InvoiceExtractionConfirm(plan_entry_id=plan.id, additional=True),
                                   db_session, user)
    assert failure.value.status_code == 422


def test_plan_line_from_another_project_is_rejected(db_session, user_factory, monkeypatch):
    user, project, document, _ = _world(db_session, user_factory)
    proposal_id = _proposal(db_session, user, project, document, monkeypatch)
    other_user, other_project, _doc, _ = _world(db_session, user_factory)
    _c, _cat, foreign_plan = _plan_line(db_session, other_project)
    with pytest.raises(HTTPException) as failure:
        confirm_invoice_extraction(proposal_id, InvoiceExtractionConfirm(plan_entry_id=foreign_plan.id), db_session, user)
    assert failure.value.status_code == 422


def test_budget_shows_invoiced_and_remaining_against_plan(db_session, user_factory, monkeypatch):
    from app.api import budget_from_dds as budget_api
    user, project, document, _ = _world(db_session, user_factory)
    proposal_id = _proposal(db_session, user, project, document, monkeypatch)
    _contract, _category, plan = _plan_line(db_session, project)
    confirm_invoice_extraction(proposal_id, InvoiceExtractionConfirm(plan_entry_id=plan.id), db_session, user)
    monkeypatch.setattr(budget_api, "require_project_role", lambda *_a, **_k: None)

    report = budget_api.budget_from_dds(project.id, contract_id=None, month_from=None, month_to=None, db=db_session, user=user)

    row = next(item for item in report["expense_by_category"] if item["category"] == "Тест СМР")
    assert row["plan"] == "500000.00"
    assert row["invoiced"] == "125400.50"
    assert row["remaining"] == "374599.50"
    assert report["direct_expense_total"]["invoiced"] == "125400.50"
