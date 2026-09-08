"""Synthetic acceptance for scoped, read-only v7 financial forecasting."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.execution_forecast.api import get_explainable_forecast
from app.execution_forecast.engine import build_forecast
from app.execution_forecast.repository import load_forecast_input
from app.api.execution_finance import overview
from app.models.execution_finance import BudgetLine, CashFlowEntry, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db, user_factory):
    owner = user_factory()
    stranger = user_factory()
    org = Organization(name="Synthetic forecast tenant")
    db.add(org); db.flush()
    project = Project(name="Synthetic forecast project", organization_id=org.id)
    other = Project(name="Synthetic other project", organization_id=org.id)
    db.add_all([project, other]); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=owner.id, role="viewer"))
    contract = Contract(project_id=project.id, number="SYN-F-1", title="Synthetic contract")
    other_contract = Contract(project_id=project.id, number="SYN-F-2", title="Other contract")
    db.add_all([contract, other_contract]); db.flush()
    baseline = ScheduleBaseline(
        project_id=project.id, contract_id=contract.id, created_by_user_id=owner.id,
        name="Approved synthetic WBS", version=1, status="approved",
    )
    db.add(baseline); db.flush()
    summary = ScheduleItem(
        project_id=project.id, baseline_id=baseline.id, title="Synthetic phase",
        is_summary=True, duration_days=None, is_milestone=None,
    )
    db.add(summary); db.flush()
    leaf = ScheduleItem(
        project_id=project.id, baseline_id=baseline.id, wbs_parent_id=summary.id,
        title="Synthetic leaf", is_summary=False, duration_days=5, is_milestone=False,
        planned_start=date(2026, 9, 1), planned_finish=date(2026, 9, 5),
    )
    other_leaf = ScheduleItem(
        project_id=project.id, baseline_id=baseline.id, wbs_parent_id=summary.id,
        title="Other leaf", is_summary=False, duration_days=2, is_milestone=False,
    )
    db.add_all([leaf, other_leaf]); db.flush()
    return owner, stranger, project, other, contract, other_contract, summary, leaf, other_leaf


def _budget(project_id, contract_id, schedule_item_id, description, amount, **overrides):
    values = dict(
        project_id=project_id, contract_id=contract_id, schedule_item_id=schedule_item_id,
        category="works", description=description, planned_amount=Decimal(amount),
        committed_amount=Decimal("0"), actual_amount=Decimal("0"), forecast_amount=Decimal(amount),
        currency="RUB", status="approved", review_status="confirmed",
    )
    values.update(overrides)
    return BudgetLine(**values)


def _cash(project_id, contract_id, schedule_item_id, title, amount, **overrides):
    values = dict(
        project_id=project_id, contract_id=contract_id, schedule_item_id=schedule_item_id,
        direction="outflow", title=title, planned_date=date(2026, 9, 10),
        planned_amount=Decimal(amount), actual_amount=Decimal("0"),
        status="approved", review_status="confirmed",
    )
    values.update(overrides)
    return CashFlowEntry(**values)


def test_only_human_confirmed_rows_are_forecast_inputs(db_session, user_factory):
    _, _, project, _, contract, _, _, leaf, _ = _world(db_session, user_factory)
    db_session.add_all([
        _budget(project.id, contract.id, leaf.id, "Confirmed", "100"),
        _budget(project.id, contract.id, leaf.id, "Needs review", "900", review_status="required"),
        _cash(project.id, contract.id, leaf.id, "Confirmed plan", "60"),
        _cash(project.id, contract.id, leaf.id, "Proposed", "700", review_status="pending_confirmation"),
    ])
    db_session.flush()

    loaded = load_forecast_input(db_session, project.id, date(2026, 9, 8))

    assert [row.description for row in loaded.budget] == ["Confirmed"]
    assert [row.title for row in loaded.cash_flow] == ["Confirmed plan"]
    result = build_forecast(loaded)
    assert result["financial_input_policy"] == "human_confirmed_budget_and_cash_flow_only"
    assert result["can_trigger_actions"] is False
    assert result["budget"]["planned_total"] == "100.00"
    assert result["cash_flow"]["planned"]["outflow"] == "60.00"
    assert result["cash_flow"]["actual"]["outflow"] == "0.00"
    assert result["cash_flow"]["forecast"]["outflow"] == "60.00"


def test_contract_and_wbs_leaf_scopes_are_exact(db_session, user_factory):
    _, _, project, _, contract, other_contract, _, leaf, other_leaf = _world(db_session, user_factory)
    db_session.add_all([
        _budget(project.id, contract.id, leaf.id, "Exact", "10"),
        _budget(project.id, contract.id, other_leaf.id, "Sibling", "20"),
        _budget(project.id, other_contract.id, None, "Other contract", "30"),
    ])
    db_session.flush()

    contract_scope = load_forecast_input(db_session, project.id, contract_id=contract.id)
    leaf_scope = load_forecast_input(
        db_session, project.id, contract_id=contract.id, schedule_item_id=leaf.id,
    )

    assert [row.description for row in contract_scope.budget] == ["Exact", "Sibling"]
    assert [row.description for row in leaf_scope.budget] == ["Exact"]
    assert leaf_scope.scope_kind == "contract_wbs_leaf"


def test_wbs_summary_aggregates_descendant_leaves_but_rejects_direct_link(db_session, user_factory):
    _, _, project, _, contract, _, summary, leaf, other_leaf = _world(db_session, user_factory)
    db_session.add_all([
        _budget(project.id, contract.id, leaf.id, "First leaf", "10"),
        _budget(project.id, contract.id, other_leaf.id, "Second leaf", "20"),
    ])
    db_session.flush()
    aggregate = load_forecast_input(db_session, project.id, schedule_item_id=summary.id)
    assert [row.description for row in aggregate.budget] == ["First leaf", "Second leaf"]
    assert aggregate.scope_kind == "wbs_summary"

    db_session.add(_budget(project.id, contract.id, summary.id, "Forbidden direct", "99"))
    db_session.flush()
    with pytest.raises(LookupError, match="schedule_summary_direct_finance_link"):
        load_forecast_input(db_session, project.id, schedule_item_id=summary.id)


def test_mixed_currency_is_never_summed_or_converted(db_session, user_factory):
    _, _, project, _, contract, _, _, leaf, _ = _world(db_session, user_factory)
    db_session.add_all([
        _budget(project.id, contract.id, leaf.id, "Roubles", "100", currency="RUB"),
        _budget(project.id, contract.id, leaf.id, "Euros", "10", currency="EUR"),
    ])
    db_session.flush()

    result = build_forecast(load_forecast_input(db_session, project.id))

    assert result["budget"]["planned_total"] is None
    assert result["budget"]["automatic_conversion"] is False
    assert result["budget"]["totals_by_currency"] == [
        {"currency": "EUR", "planned_amount": "10.00", "actual_amount": "0.00",
         "forecast_amount": "10.00", "variance": "0.00"},
        {"currency": "RUB", "planned_amount": "100.00", "actual_amount": "0.00",
         "forecast_amount": "100.00", "variance": "0.00"},
    ]
    codes = {row["code"] for row in result["decision_requirements"]}
    assert {"unknown_currency", "currency_conversion_policy", "exchange_rate_source", "mixed_currency"} <= codes


def test_row_limit_fails_closed_without_truncating(db_session, user_factory):
    _, _, project, _, contract, _, _, leaf, _ = _world(db_session, user_factory)
    db_session.add_all([
        _budget(project.id, contract.id, leaf.id, f"Row {index}", "1") for index in range(3)
    ])
    db_session.flush()
    with pytest.raises(LookupError, match="forecast_row_limit_exceeded"):
        load_forecast_input(db_session, project.id, row_limit=2)


def test_api_enforces_project_acl_before_scope_lookup(db_session, user_factory):
    _, stranger, project, _, contract, _, _, leaf, _ = _world(db_session, user_factory)
    with pytest.raises(HTTPException) as caught:
        get_explainable_forecast(
            project.id, date(2026, 9, 8), contract.id, leaf.id, 20, db_session, stranger,
        )
    assert caught.value.status_code == 403


def test_other_project_scope_ids_are_not_accepted(db_session, user_factory):
    _, _, project, other, _, _, _, _, _ = _world(db_session, user_factory)
    foreign = Contract(project_id=other.id, number="FOREIGN", title="Foreign synthetic")
    db_session.add(foreign); db_session.flush()
    with pytest.raises(LookupError, match="contract_not_found"):
        load_forecast_input(db_session, project.id, contract_id=foreign.id)


def test_execution_overview_exposes_lossless_wbs_shape(db_session, user_factory):
    owner, _, project, _, _, _, summary, leaf, _ = _world(db_session, user_factory)
    db_session.commit()

    payload = overview(project.id, db_session, owner)
    by_id = {row["id"]: row for row in payload["schedule"]}

    assert by_id[summary.id]["wbs_parent_id"] is None
    assert by_id[summary.id]["wbs_level"] == 0
    assert by_id[summary.id]["is_summary"] is True
    assert by_id[leaf.id]["wbs_parent_id"] == summary.id
    assert by_id[leaf.id]["wbs_level"] == 1
    assert by_id[leaf.id]["is_summary"] is False
