from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.api.execution_finance as execution_finance
from app.api.execution_finance import CashFlowCreate, create_cash_flow, overview
from app.api.projects import ProjectCreate
from app.finance_money import from_minor_units, money, money_string, to_minor_units
from app.models.execution_finance import BudgetLine, CashFlowEntry
from app.models.organization_contract import Organization
from app.models.project import Project
from app.money_migration_audit import assert_money_dry_run_safe, build_money_dry_run_report


def _project(db_session, user_factory, *, currency="RUB", project_id=None):
    organization = Organization(name="V6-00a organization")
    user = user_factory(is_admin=True)
    db_session.add(organization)
    db_session.flush()
    project = Project(
        id=project_id,
        name="V6-00a project",
        organization_id=organization.id,
        currency=currency,
    )
    db_session.add(project)
    db_session.flush()
    return user, project


def test_money_boundary_is_half_up_exact_and_rejects_float():
    assert money("1.005") == Decimal("1.01")
    assert to_minor_units("125400.50") == 12_540_050
    assert from_minor_units(12_540_050) == Decimal("125400.50")
    assert money_string("125400.5") == "125400.50"
    with pytest.raises(ValueError, match="не float"):
        money(1.005)


def test_new_projects_default_to_rub_and_currency_is_strict():
    assert ProjectCreate(name="Проект").currency == "RUB"
    with pytest.raises(ValidationError):
        ProjectCreate(name="Проект", currency="rub")


def test_cash_flow_materialization_rejects_currency_outside_project(
    db_session, user_factory, monkeypatch,
):
    user, project = _project(db_session, user_factory)
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        create_cash_flow(
            CashFlowCreate(
                project_id=project.id,
                direction="outflow",
                title="Счёт в другой валюте",
                planned_date="2026-09-25",
                planned_amount="100.00",
                currency="USD",
            ),
            db_session,
            user,
        )

    assert error.value.status_code == 422
    assert error.value.detail == "Валюта USD не совпадает с валютой проекта RUB"
    assert db_session.query(CashFlowEntry).filter_by(project_id=project.id).count() == 0


def test_legacy_overview_fails_closed_on_mixed_project_currency(
    db_session, user_factory, monkeypatch,
):
    user, project = _project(db_session, user_factory)
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)
    db_session.add(BudgetLine(
        project_id=project.id,
        category="A",
        description="Legacy mismatch",
        planned_amount=Decimal("2.00"),
        forecast_amount=Decimal("2.00"),
        currency="USD",
        status="approved",
    ))
    db_session.flush()

    with pytest.raises(HTTPException) as error:
        overview(project.id, db_session, user)

    assert error.value.status_code == 409
    assert error.value.detail.startswith("PROJECT_CURRENCY_MISMATCH:")


def test_project_17_dry_run_reports_every_value_and_stops_on_one_kopeck(
    db_session, user_factory,
):
    _user, project = _project(db_session, user_factory, project_id=17)
    db_session.add(CashFlowEntry(
        project_id=project.id,
        direction="outflow",
        title="Проверка",
        planned_date=date(2026, 9, 25),
        planned_amount=Decimal("125400.50"),
        actual_amount=Decimal("100.00"),
        currency="RUB",
        status="approved",
    ))
    db_session.flush()

    report = build_money_dry_run_report(db_session, project_id=17)

    assert report["status"] == "PASS"
    assert report["changed_value_count"] == 0
    assert report["max_abs_delta"] == "0.00"
    assert {
        (row["table"], row["field"], row["before"], row["minor_units"], row["after"])
        for row in report["rows"]
    } >= {
        ("cash_flow_entries", "planned_amount", "125400.50", "12540050", "125400.50"),
        ("cash_flow_entries", "actual_amount", "100.00", "10000", "100.00"),
    }

    report["status"] = "STOP"
    report["changed_value_count"] = 1
    report["max_abs_delta"] = "0.01"
    with pytest.raises(RuntimeError, match="0.01"):
        assert_money_dry_run_safe(report)
