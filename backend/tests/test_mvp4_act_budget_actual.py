from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

import app.api.execution_finance as finance_api
from app.api.execution_finance import (
    ActCreate,
    PaymentConfirmation,
    StatusUpdate,
    confirm_payment,
    create_act,
    overview,
    update_status,
)
from app.models.audit_log import AuditLog
from app.models.execution_finance import AcceptanceAct, BudgetLine, CashFlowEntry
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db, user_factory, *, role="manager"):
    organization = Organization(name="Act actual tenant")
    user = user_factory(is_admin=False)
    db.add(organization); db.flush()
    project = Project(name="Act actual project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    contract = Contract(project_id=project.id, number="C-1", title="Works", status="active")
    db.add(contract); db.flush()
    budget = BudgetLine(
        project_id=project.id, contract_id=contract.id, category="Works",
        description="Concrete", planned_amount=Decimal("100.00"),
        committed_amount=Decimal("0.00"), actual_amount=Decimal("0.00"),
        forecast_amount=Decimal("100.00"), currency="RUB", status="approved",
    )
    db.add(budget); db.flush()
    return user, project, contract, budget


def _act(db, project, contract, budget, *, amount="40.00", status="proposed"):
    row = AcceptanceAct(
        project_id=project.id, contract_id=contract.id, budget_line_id=budget.id,
        number="A-1", title="Accepted work", amount=Decimal(amount),
        currency="RUB", status=status,
    )
    db.add(row); db.flush()
    return row


def _status(db, user, act, value):
    return update_status("acts", act.id, StatusUpdate(status=value), db, user)


def test_create_linked_act_and_overview_expose_budget_projection_fields(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    created = create_act(ActCreate(
        project_id=project.id, contract_id=contract.id, budget_line_id=budget.id,
        number="A-2", title="Linked act", amount="25.00", currency="RUB",
    ), db_session, user)

    result = overview(project.id, db_session, user)
    act = next(row for row in result["acts"] if row["id"] == created["id"])
    projected = next(row for row in result["budget"] if row["id"] == budget.id)
    assert act["budget_line_id"] == budget.id
    assert projected["remaining_amount"] == Decimal("100.00")
    assert projected["overrun_amount"] == Decimal("0")


def test_act_budget_link_rejects_another_project(db_session, user_factory, monkeypatch):
    user, project, contract, _budget = _world(db_session, user_factory)
    _other_user, _other_project, _other_contract, other_budget = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    with pytest.raises(HTTPException) as error:
        create_act(ActCreate(
            project_id=project.id, contract_id=contract.id, budget_line_id=other_budget.id,
            number="A-X", title="Cross tenant", amount="10.00",
        ), db_session, user)
    assert error.value.status_code == 422
    assert "проекту" in error.value.detail


def test_act_budget_link_rejects_another_contract(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    other = Contract(project_id=project.id, number="C-2", title="Other", status="active")
    db_session.add(other); db_session.flush()
    with pytest.raises(HTTPException) as error:
        create_act(ActCreate(
            project_id=project.id, contract_id=other.id, budget_line_id=budget.id,
            number="A-X", title="Wrong contract", amount="10.00",
        ), db_session, user)
    assert error.value.status_code == 422
    assert "договору" in error.value.detail


def test_act_budget_link_rejects_currency_mismatch(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    with pytest.raises(HTTPException) as error:
        create_act(ActCreate(
            project_id=project.id, contract_id=contract.id, budget_line_id=budget.id,
            number="A-X", title="Wrong currency", amount="10.00", currency="USD",
        ), db_session, user)
    assert error.value.status_code == 422
    assert "Валюта" in error.value.detail


def test_approval_does_not_change_budget_actual(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    act = _act(db_session, project, contract, budget)
    _status(db_session, user, act, "approved")
    assert budget.actual_amount == Decimal("0.00")


def test_signed_act_sets_budget_actual_and_audits_projection(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    act = _act(db_session, project, contract, budget)
    _status(db_session, user, act, "approved")
    result = _status(db_session, user, act, "signed")
    assert budget.actual_amount == Decimal("40.00")
    assert result["budget_remaining_amount"] == Decimal("60.00")
    audit = db_session.query(AuditLog).filter(AuditLog.entity_id == act.id).order_by(AuditLog.id.desc()).first()
    assert "budget_actual_after=40.00" in audit.details


def test_two_signed_acts_are_summed(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    first = _act(db_session, project, contract, budget, amount="40.00")
    second = _act(db_session, project, contract, budget, amount="35.00")
    for row in (first, second):
        _status(db_session, user, row, "approved")
        _status(db_session, user, row, "signed")
    assert budget.actual_amount == Decimal("75.00")


def test_repeated_signing_is_idempotent(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    act = _act(db_session, project, contract, budget)
    _status(db_session, user, act, "approved")
    _status(db_session, user, act, "signed")
    repeated = _status(db_session, user, act, "signed")
    assert repeated["budget_actual_amount"] == Decimal("40.00")
    assert budget.actual_amount == Decimal("40.00")


def test_revoke_and_resign_recompute_exactly_once(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    act = _act(db_session, project, contract, budget)
    _status(db_session, user, act, "approved")
    _status(db_session, user, act, "signed")
    _status(db_session, user, act, "approved")
    assert budget.actual_amount == Decimal("0.00")
    _status(db_session, user, act, "signed")
    assert budget.actual_amount == Decimal("40.00")


def test_paid_legacy_status_remains_in_actual_projection(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    act = _act(db_session, project, contract, budget)
    _status(db_session, user, act, "approved")
    _status(db_session, user, act, "signed")
    _status(db_session, user, act, "paid")
    assert budget.actual_amount == Decimal("40.00")
    assert db_session.query(CashFlowEntry).count() == 0


def test_overrun_warns_without_blocking_signature(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    act = _act(db_session, project, contract, budget, amount="125.00")
    _status(db_session, user, act, "approved")
    result = _status(db_session, user, act, "signed")
    assert result["status"] == "signed"
    assert result["budget_remaining_amount"] == Decimal("-25.00")
    assert result["budget_overrun_amount"] == Decimal("25.00")
    assert result["budget_warning"] == "BUDGET_ACTUAL_EXCEEDED"


def test_payment_confirmation_changes_committed_not_act_actual(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    act = _act(db_session, project, contract, budget)
    _status(db_session, user, act, "approved")
    _status(db_session, user, act, "signed")
    payment = CashFlowEntry(
        project_id=project.id, contract_id=contract.id, budget_line_id=budget.id,
        direction="outflow", title="Payable", planned_date=date(2026, 9, 22),
        planned_amount=Decimal("30.00"), actual_amount=Decimal("0.00"),
        currency="RUB", status="approved",
    )
    db_session.add(payment); db_session.commit()
    confirm_payment(
        payment.id,
        PaymentConfirmation(actual_amount="30.00", actual_date="2026-09-22"),
        db_session, user,
    )
    assert budget.committed_amount == Decimal("30.00")
    assert budget.actual_amount == Decimal("40.00")


def test_state_machine_rejects_direct_proposed_to_signed(db_session, user_factory):
    user, project, contract, budget = _world(db_session, user_factory)
    act = _act(db_session, project, contract, budget)
    with pytest.raises(HTTPException) as error:
        _status(db_session, user, act, "signed")
    assert error.value.status_code == 409
    assert act.status == "proposed"
    assert budget.actual_amount == Decimal("0.00")


def test_editor_cannot_approve_or_sign_act(db_session, user_factory):
    editor, project, contract, budget = _world(db_session, user_factory, role="editor")
    act = _act(db_session, project, contract, budget)
    with pytest.raises(HTTPException) as error:
        _status(db_session, editor, act, "approved")
    assert error.value.status_code == 403
