"""Tests for ADR-V6-05-INCOME-BUDGET-RU: explicit direction on BudgetLine.

Covers the owner's decisions:
1. direction is an explicit, independent field on BudgetLine.
2. a dedicated income budget line exists (the per-contract contract_control
   row, once direction-aware).
3. a mismatch between a line's direction and its contract's role is a hard
   422, never a warning.
4. legacy_unclassified backfill/creation stays direction=NULL.
5. project margin = sum(inflow) - sum(outflow) over confirmed lines, and the
   pre-existing budget_planned/committed/actual/forecast totals keep their
   historical (expense-only) meaning.
"""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.execution_finance import BudgetCreate, create_budget, overview
from app.api.organizations_contracts import confirm_contract_budget_proposal, create_contract_budget_proposal
from app.models.execution_finance import BudgetLine, ScheduleBaseline
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _project(db, user_factory, *, role="editor"):
    organization = Organization(name="Income budget tenant")
    user = user_factory()
    db.add(organization); db.flush()
    project = Project(name="Income budget project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    db.flush()
    return user, project


def _contract(db, project, kind, amount="1000.00"):
    contract = Contract(
        project_id=project.id, number=f"C-{kind}", title=kind, contract_kind=kind,
        amount=Decimal(amount), status="active",
    )
    db.add(contract); db.flush()
    return contract


def test_income_budget_line_on_customer_contract_is_accepted(db_session, user_factory):
    user, project = _project(db_session, user_factory)
    contract = _contract(db_session, project, "customer")

    result = create_budget(BudgetCreate(
        project_id=project.id, contract_id=contract.id, direction="inflow",
        category="Доход", description="Плановый доход по этапу 1", planned_amount="500000",
    ), db_session, user)

    line = db_session.get(BudgetLine, result["id"])
    assert line.direction == "inflow"


def test_income_direction_on_expense_contract_is_rejected(db_session, user_factory):
    user, project = _project(db_session, user_factory)
    contract = _contract(db_session, project, "supply")

    with pytest.raises(HTTPException) as error:
        create_budget(BudgetCreate(
            project_id=project.id, contract_id=contract.id, direction="inflow",
            description="Неверное направление", planned_amount="100",
        ), db_session, user)
    assert error.value.status_code == 422
    assert "BUDGET_DIRECTION_CONTRACT_MISMATCH" in str(error.value.detail)


def test_outflow_direction_on_income_contract_is_rejected(db_session, user_factory):
    user, project = _project(db_session, user_factory)
    contract = _contract(db_session, project, "revenue_subcontract")

    with pytest.raises(HTTPException) as error:
        create_budget(BudgetCreate(
            project_id=project.id, contract_id=contract.id, direction="outflow",
            description="Неверное направление", planned_amount="100",
        ), db_session, user)
    assert error.value.status_code == 422


def test_direction_without_a_contract_is_rejected_not_guessed(db_session, user_factory):
    user, project = _project(db_session, user_factory)

    with pytest.raises(HTTPException) as error:
        create_budget(BudgetCreate(
            project_id=project.id, contract_id=None, direction="inflow",
            description="Без договора", planned_amount="100",
        ), db_session, user)
    assert error.value.status_code == 422


def test_legacy_budget_line_without_direction_is_unaffected(db_session, user_factory):
    user, project = _project(db_session, user_factory)
    contract = _contract(db_session, project, "supply")

    result = create_budget(BudgetCreate(
        project_id=project.id, contract_id=contract.id, category="Материалы",
        description="Старый способ, без направления", planned_amount="100",
    ), db_session, user)

    line = db_session.get(BudgetLine, result["id"])
    assert line.direction is None
    assert line.line_kind == "legacy_unclassified"


@pytest.mark.parametrize("kind,expected", [
    ("customer", "inflow"),
    ("revenue_subcontract", "inflow"),
    ("downstream_subcontract", "outflow"),
    ("supply", "outflow"),
])
def test_contract_control_line_is_tagged_with_the_contracts_role(db_session, user_factory, kind, expected):
    user, project = _project(db_session, user_factory, role="manager")
    contract = _contract(db_session, project, kind)
    from app.models.execution_finance import CostCategory
    category = CostCategory(organization_id=project.organization_id, name="Materials", normalized_name="materials", is_active=True)
    db_session.add(category); db_session.flush()

    proposal = create_contract_budget_proposal(project.id, contract.id, db_session, user)
    from app.api.organizations_contracts import ContractBudgetProposalUpdate, update_contract_budget_proposal
    update_contract_budget_proposal(
        proposal["id"], ContractBudgetProposalUpdate(selected_cost_category_id=category.id), db_session, user,
    )
    result = confirm_contract_budget_proposal(proposal["id"], db_session, user)

    line = db_session.get(BudgetLine, result["created_budget_line_id"])
    assert line.direction == expected
    assert line.line_kind == "contract_control"


def test_project_margin_separates_income_from_expense_and_keeps_legacy_totals_unchanged(db_session, user_factory):
    user, project = _project(db_session, user_factory, role="viewer")
    income_contract = _contract(db_session, project, "customer")
    expense_contract = _contract(db_session, project, "supply")
    db_session.add(ScheduleBaseline(project_id=project.id, created_by_user_id=user.id, name="ГПР", version=1, status="approved"))
    db_session.flush()

    income_line = BudgetLine(
        project_id=project.id, contract_id=income_contract.id, line_kind="contract_control",
        direction="inflow", category="Доход", description="Доход по этапу", planned_amount=Decimal("1000"),
        currency="RUB", status="approved",
    )
    expense_line = BudgetLine(
        project_id=project.id, contract_id=expense_contract.id, line_kind="contract_control",
        direction="outflow", category="Материалы", description="Материалы", planned_amount=Decimal("400"),
        currency="RUB", status="approved",
    )
    legacy_line = BudgetLine(
        project_id=project.id, contract_id=expense_contract.id, line_kind="legacy_unclassified",
        direction=None, category="Старое", description="Старая строка без направления",
        planned_amount=Decimal("50"), currency="RUB", status="approved",
    )
    db_session.add_all([income_line, expense_line, legacy_line])
    db_session.flush()

    result = overview(project.id, db_session, user)
    summary = result["summary"]

    # Historical totals: legacy (direction=NULL) still counts as expense-side,
    # exactly as before this ADR -- only the new inflow line is excluded.
    assert summary["budget_planned"] == Decimal("450")
    assert summary["budget_income_planned"] == Decimal("1000")
    # Margin excludes the legacy_unclassified row (decision 4): 1000 - 400, not 1000 - 450.
    assert summary["budget_margin_planned"] == Decimal("600")
