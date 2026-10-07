"""ADR-V6-05-INCOME-BUDGET-RU: narrow backfill of budget_line_id onto an
already-approved cash-flow entry that has none -- found live during the
V6-00 walkthrough on project #17 (11 historical income entries, approved
long before any income budget line could exist to link them to).
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.execution_finance import ApprovedBudgetLineBackfill, link_approved_cash_flow_budget_line
from app.models.execution_finance import BudgetLine, CashFlowEntry
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db, user_factory, *, role="manager", kind="revenue_subcontract"):
    organization = Organization(name="Backfill tenant")
    user = user_factory()
    db.add(organization); db.flush()
    project = Project(name="Backfill project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    contract = Contract(project_id=project.id, number="C-1", title=kind, contract_kind=kind, status="active")
    db.add(contract); db.flush()
    return user, project, contract


def _approved_entry(db, project, contract, *, direction="inflow", budget_line_id=None):
    entry = CashFlowEntry(
        project_id=project.id, contract_id=contract.id, direction=direction,
        title="Historical entry", planned_date=date(2026, 1, 31), planned_amount=Decimal("100"),
        currency="RUB", status="approved", category="Прочее", budget_line_id=budget_line_id,
    )
    db.add(entry); db.flush()
    return entry


def _budget_line(db, project, contract, *, direction="inflow", currency="RUB"):
    line = BudgetLine(
        project_id=project.id, contract_id=contract.id, line_kind="contract_control",
        direction=direction, category="Выручка", description="Доходная строка",
        planned_amount=Decimal("1000"), currency=currency, status="approved",
    )
    db.add(line); db.flush()
    return line


def test_backfills_an_empty_budget_line_on_an_approved_entry(db_session, user_factory):
    user, project, contract = _world(db_session, user_factory)
    entry = _approved_entry(db_session, project, contract)
    budget = _budget_line(db_session, project, contract)

    result = link_approved_cash_flow_budget_line(
        entry.id, ApprovedBudgetLineBackfill(project_id=project.id, budget_line_id=budget.id),
        db_session, user,
    )

    assert result["budget_line_id"] == budget.id
    db_session.refresh(entry)
    assert entry.budget_line_id == budget.id
    assert entry.status == "approved"  # untouched


def test_refuses_to_overwrite_an_existing_link(db_session, user_factory):
    user, project, contract = _world(db_session, user_factory)
    other_budget = _budget_line(db_session, project, contract)
    entry = _approved_entry(db_session, project, contract, budget_line_id=other_budget.id)
    new_budget = _budget_line(db_session, project, contract)

    with pytest.raises(HTTPException) as error:
        link_approved_cash_flow_budget_line(
            entry.id, ApprovedBudgetLineBackfill(project_id=project.id, budget_line_id=new_budget.id),
            db_session, user,
        )
    assert error.value.status_code == 409
    db_session.refresh(entry)
    assert entry.budget_line_id == other_budget.id


def test_refuses_a_proposed_entry_use_link_controls_instead(db_session, user_factory):
    user, project, contract = _world(db_session, user_factory)
    entry = _approved_entry(db_session, project, contract)
    entry.status = "proposed"
    db_session.flush()
    budget = _budget_line(db_session, project, contract)

    with pytest.raises(HTTPException) as error:
        link_approved_cash_flow_budget_line(
            entry.id, ApprovedBudgetLineBackfill(project_id=project.id, budget_line_id=budget.id),
            db_session, user,
        )
    assert error.value.status_code == 409


def test_refuses_a_direction_mismatch(db_session, user_factory):
    user, project, contract = _world(db_session, user_factory)
    entry = _approved_entry(db_session, project, contract, direction="inflow")
    wrong_budget = _budget_line(db_session, project, contract, direction="outflow")

    with pytest.raises(HTTPException) as error:
        link_approved_cash_flow_budget_line(
            entry.id, ApprovedBudgetLineBackfill(project_id=project.id, budget_line_id=wrong_budget.id),
            db_session, user,
        )
    assert error.value.status_code == 422
    assert "BUDGET_DIRECTION_MISMATCH" in str(error.value.detail)


def test_refuses_a_budget_line_from_a_different_contract(db_session, user_factory):
    user, project, contract = _world(db_session, user_factory)
    other_contract = Contract(project_id=project.id, number="C-2", title="Other", contract_kind="customer", status="active")
    db_session.add(other_contract); db_session.flush()
    entry = _approved_entry(db_session, project, contract)
    budget = _budget_line(db_session, project, other_contract)

    with pytest.raises(HTTPException) as error:
        link_approved_cash_flow_budget_line(
            entry.id, ApprovedBudgetLineBackfill(project_id=project.id, budget_line_id=budget.id),
            db_session, user,
        )
    assert error.value.status_code == 422


def test_refuses_a_currency_mismatch(db_session, user_factory):
    user, project, contract = _world(db_session, user_factory)
    entry = _approved_entry(db_session, project, contract)
    budget = _budget_line(db_session, project, contract, currency="USD")

    with pytest.raises(HTTPException) as error:
        link_approved_cash_flow_budget_line(
            entry.id, ApprovedBudgetLineBackfill(project_id=project.id, budget_line_id=budget.id),
            db_session, user,
        )
    assert error.value.status_code == 422
