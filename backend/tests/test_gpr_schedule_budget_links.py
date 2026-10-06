"""Tests for ADR-GPR-PER-CONTRACT-BUDGET-ALLOCATION-RU: schedule_budget_links.

Covers the owner's decisions:
1. new many-to-many table (budget_line_id, schedule_item_id, amount).
2. amount only.
3. allocations may never sum above the budget line's planned_amount -- hard
   reject, not a warning.
4. (migration/model-level, not exercised here) a new baseline revision has
   new ScheduleItem rows, so nothing here is "carried over".
5. committed_amount/actual_amount are untouched by this table.
"""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.execution_finance import ScheduleBudgetLinkCreate, create_schedule_budget_link, delete_schedule_budget_link
from app.models.execution_finance import BudgetLine, ScheduleBaseline, ScheduleBudgetLink, ScheduleItem
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db, user_factory, *, role="editor", planned_amount="1000"):
    organization = Organization(name="GPR allocation tenant")
    user = user_factory()
    db.add(organization); db.flush()
    project = Project(name="GPR allocation project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    contract = Contract(project_id=project.id, number="C-1", title="Supply", contract_kind="supply", status="active")
    db.add(contract); db.flush()
    budget_line = BudgetLine(
        project_id=project.id, contract_id=contract.id, line_kind="contract_control", direction="outflow",
        category="Materials", description="Контроль по договору", planned_amount=Decimal(planned_amount),
        currency="RUB", status="approved",
    )
    baseline = ScheduleBaseline(project_id=project.id, contract_id=contract.id, created_by_user_id=user.id, name="ГПР", version=1, status="approved")
    db.add_all([budget_line, baseline]); db.flush()
    stage = ScheduleItem(project_id=project.id, baseline_id=baseline.id, title="Этап 1", sort_order=1, duration_days=1)
    db.add(stage); db.flush()
    return user, project, contract, budget_line, baseline, stage


def test_allocation_within_budget_is_accepted(db_session, user_factory):
    user, project, _contract, budget_line, _baseline, stage = _world(db_session, user_factory)

    result = create_schedule_budget_link(ScheduleBudgetLinkCreate(
        project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=stage.id, amount="400",
    ), db_session, user)

    link = db_session.get(ScheduleBudgetLink, result["id"])
    assert link.amount == Decimal("400")


def test_allocations_cannot_sum_above_the_budget_line(db_session, user_factory):
    user, project, _contract, budget_line, baseline, stage = _world(db_session, user_factory, planned_amount="500")
    other_stage = ScheduleItem(project_id=project.id, baseline_id=baseline.id, title="Этап 2", sort_order=2, duration_days=1)
    db_session.add(other_stage); db_session.flush()
    create_schedule_budget_link(ScheduleBudgetLinkCreate(
        project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=stage.id, amount="400",
    ), db_session, user)

    with pytest.raises(HTTPException) as error:
        create_schedule_budget_link(ScheduleBudgetLinkCreate(
            project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=other_stage.id, amount="200",
        ), db_session, user)
    assert error.value.status_code == 409
    assert "BUDGET_ALLOCATION_EXCEEDS_LINE" in str(error.value.detail)


def test_allocation_exactly_equal_to_budget_is_allowed(db_session, user_factory):
    user, project, _contract, budget_line, _baseline, stage = _world(db_session, user_factory, planned_amount="400")

    result = create_schedule_budget_link(ScheduleBudgetLinkCreate(
        project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=stage.id, amount="400",
    ), db_session, user)
    assert result["amount"] == Decimal("400")


def test_schedule_item_from_a_different_contract_is_rejected(db_session, user_factory):
    user, project, _contract, budget_line, _baseline, _stage = _world(db_session, user_factory)
    other_contract = Contract(project_id=project.id, number="C-2", title="Other", contract_kind="supply", status="active")
    db_session.add(other_contract); db_session.flush()
    other_baseline = ScheduleBaseline(project_id=project.id, contract_id=other_contract.id, created_by_user_id=user.id, name="ГПР 2", version=2, status="approved")
    db_session.add(other_baseline); db_session.flush()
    other_stage = ScheduleItem(project_id=project.id, baseline_id=other_baseline.id, title="Другой этап", sort_order=1, duration_days=1)
    db_session.add(other_stage); db_session.flush()

    with pytest.raises(HTTPException) as error:
        create_schedule_budget_link(ScheduleBudgetLinkCreate(
            project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=other_stage.id, amount="100",
        ), db_session, user)
    assert error.value.status_code == 422
    assert "SCHEDULE_BUDGET_LINK_CONTRACT_MISMATCH" in str(error.value.detail)


def test_schedule_item_from_a_different_project_is_rejected(db_session, user_factory):
    user, project, _contract, budget_line, _baseline, _stage = _world(db_session, user_factory)
    other_project = Project(name="Other project", organization_id=project.organization_id)
    db_session.add(other_project); db_session.flush()
    db_session.add(ProjectMember(project_id=other_project.id, user_id=user.id, role="editor"))
    other_baseline = ScheduleBaseline(project_id=other_project.id, created_by_user_id=user.id, name="ГПР", version=1, status="approved")
    db_session.add(other_baseline); db_session.flush()
    other_stage = ScheduleItem(project_id=other_project.id, baseline_id=other_baseline.id, title="Этап", sort_order=1, duration_days=1)
    db_session.add(other_stage); db_session.flush()

    with pytest.raises(HTTPException) as error:
        create_schedule_budget_link(ScheduleBudgetLinkCreate(
            project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=other_stage.id, amount="100",
        ), db_session, user)
    assert error.value.status_code == 422


def test_deleting_an_allocation_frees_room_for_a_new_one(db_session, user_factory):
    user, project, _contract, budget_line, _baseline, stage = _world(db_session, user_factory, planned_amount="400")
    created = create_schedule_budget_link(ScheduleBudgetLinkCreate(
        project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=stage.id, amount="400",
    ), db_session, user)

    with pytest.raises(HTTPException):
        create_schedule_budget_link(ScheduleBudgetLinkCreate(
            project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=stage.id, amount="1",
        ), db_session, user)

    delete_schedule_budget_link(created["id"], db_session, user)
    assert db_session.get(ScheduleBudgetLink, created["id"]) is None

    second = create_schedule_budget_link(ScheduleBudgetLinkCreate(
        project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=stage.id, amount="400",
    ), db_session, user)
    assert second["amount"] == Decimal("400")


def test_allocation_does_not_touch_committed_or_actual_amount(db_session, user_factory):
    user, project, _contract, budget_line, _baseline, stage = _world(db_session, user_factory)
    budget_line.committed_amount = Decimal("777")
    budget_line.actual_amount = Decimal("111")
    db_session.flush()

    create_schedule_budget_link(ScheduleBudgetLinkCreate(
        project_id=project.id, budget_line_id=budget_line.id, schedule_item_id=stage.id, amount="400",
    ), db_session, user)

    db_session.refresh(budget_line)
    assert budget_line.committed_amount == Decimal("777")
    assert budget_line.actual_amount == Decimal("111")
