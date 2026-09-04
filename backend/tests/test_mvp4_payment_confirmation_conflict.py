from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

import app.api.execution_finance as execution_finance
from app.api.execution_finance import PaymentConfirmation, confirm_payment
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Organization
from app.models.project import Project


def _confirmed_payment(db_session, user_factory):
    organization = Organization(name="MVP4 payment confirmation test")
    user = user_factory()
    db_session.add(organization)
    db_session.flush()
    project = Project(name="MVP4 finance project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    payment = CashFlowEntry(
        project_id=project.id,
        direction="outflow",
        title="Оплаченный счёт",
        planned_date=date(2026, 9, 10),
        planned_amount=Decimal("75000.00"),
        actual_date=date(2026, 9, 11),
        actual_amount=Decimal("74250.00"),
        status="paid",
    )
    db_session.add(payment)
    db_session.flush()
    return user, payment


def test_repeat_confirmation_with_same_fact_is_idempotent(db_session, user_factory, monkeypatch):
    user, payment = _confirmed_payment(db_session, user_factory)
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)

    result = confirm_payment(
        payment.id,
        PaymentConfirmation(actual_amount="74250.00", actual_date="2026-09-11"),
        db_session,
        user,
    )

    assert result == {"id": payment.id, "status": "paid", "already_confirmed": True}


@pytest.mark.parametrize(
    "confirmation",
    [
        PaymentConfirmation(actual_amount="75000.00", actual_date="2026-09-11"),
        PaymentConfirmation(actual_amount="74250.00", actual_date="2026-09-12"),
    ],
)
def test_repeat_confirmation_with_conflicting_fact_requires_correction(
    db_session,
    user_factory,
    monkeypatch,
    confirmation,
):
    user, payment = _confirmed_payment(db_session, user_factory)
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        confirm_payment(payment.id, confirmation, db_session, user)

    assert error.value.status_code == 409
    assert "корректирующее событие" in error.value.detail
