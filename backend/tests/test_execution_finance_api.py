from decimal import Decimal
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.api.execution_finance as execution_finance
from app.api.execution_finance import (
    BaselineCreate,
    BudgetCreate,
    CashFlowCreate,
    InvoiceProposalCreate,
    PaymentConfirmation,
    PaymentCorrection,
    ScheduleItemCreate,
    StatusUpdate,
    _finance_document_hints,
    _finance_document_score,
    _linked_budget_totals,
    confirm_payment,
    correct_payment,
    router,
)
from app.models.execution_finance import CashFlowEntry
from app.models.audit_log import AuditLog
from app.models.organization_contract import Organization
from app.models.project import Project


def test_mvp4_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/execution/overview" in paths
    assert "/execution/document-candidates" in paths
    assert "/execution/baselines" in paths
    assert "/execution/schedule-items" in paths
    assert "/execution/schedule-items/{item_id}" in paths
    assert "/execution/budget" in paths
    assert "/execution/cash-flow" in paths
    assert "/execution/invoice-proposals" in paths
    assert "/execution/cash-flow/{item_id}/confirm-payment" in paths
    assert "/execution/cash-flow/{item_id}/correct-payment" in paths
    assert "/execution/procurement" in paths
    assert "/execution/acts" in paths
    assert "/execution/{kind}/{item_id}/status" in paths


def test_mvp4_request_contracts_preserve_plan_and_fact():
    baseline = BaselineCreate(project_id=7, contract_id=11, name="ГПР редакция 1")
    schedule = ScheduleItemCreate(baseline_id=3, title="Монтаж", planned_progress=80)
    budget = BudgetCreate(project_id=7, category="СМР", description="Монтаж", planned_amount="120000")
    cash = CashFlowCreate(project_id=7, direction="outflow", title="Аванс", planned_date="2026-09-01", planned_amount="50000")
    actual = StatusUpdate(status="active", actual_amount="42000", actual_date="2026-09-02")

    assert baseline.note is None
    assert baseline.contract_id == 11
    assert schedule.planned_progress == 80
    assert budget.planned_amount == Decimal("120000")
    assert cash.direction == "outflow"
    assert actual.actual_amount == Decimal("42000")


def test_invoice_proposal_preserves_control_links_and_manual_fact():
    proposal = InvoiceProposalCreate(
        project_id=7,
        contract_id=11,
        schedule_item_id=13,
        budget_line_id=17,
        source_document_id=19,
        direction="outflow",
        title="Счёт за оборудование",
        planned_date="2026-09-10",
        planned_amount="75000",
    )
    confirmation = PaymentConfirmation(actual_amount="74250", actual_date="2026-09-11")

    assert proposal.schedule_item_id == 13
    assert proposal.budget_line_id == 17
    assert proposal.source_document_id == 19
    assert confirmation.actual_amount == Decimal("74250")


def test_linked_budget_totals_are_idempotent_and_ignore_cancelled_entries():
    class Entry:
        def __init__(self, status, planned, actual="0", direction="outflow"):
            self.status = status
            self.planned_amount = Decimal(planned)
            self.actual_amount = Decimal(actual)
            self.direction = direction

    committed, actual = _linked_budget_totals([
        Entry("approved", "100"),
        Entry("paid", "200", "190"),
        Entry("cancelled", "300"),
        Entry("received", "400", "400", "inflow"),
    ])
    assert committed == Decimal("300")
    assert actual == Decimal("190")


def test_paid_status_is_reserved_for_explicit_payment_confirmation():
    source = __import__("inspect").getsource(__import__("app.api.execution_finance", fromlist=["update_status"]).update_status)
    assert '"cash-flow": {"approved", "cancelled"}' in source


def test_finance_document_candidates_are_explainable_and_extract_hints():
    score, reasons = _finance_document_score(
        "скан_0042.pdf",
        "СЧЕТ НА ОПЛАТУ № 57 от 28.08.2026. Итого к оплате 125 400,50 руб.",
        "invoice",
    )
    hints = _finance_document_hints(
        "скан_0042.pdf",
        "СЧЕТ НА ОПЛАТУ № 57 от 28.08.2026. Итого к оплате 125 400,50 руб.",
    )

    assert score >= 40
    assert any("тексте" in reason for reason in reasons)
    assert hints == {"amount": "125400.50", "date": "2026-08-28", "number": "57"}


def test_finance_document_classifier_distinguishes_schedule_and_act():
    schedule_score, _ = _finance_document_score("Приложение №3 График.docx", "Календарный план", "schedule")
    act_score, _ = _finance_document_score("КС-2 июль.pdf", "Акт выполненных работ", "act")
    wrong_score, _ = _finance_document_score("КС-2 июль.pdf", "Акт выполненных работ", "invoice")

    assert schedule_score >= 50
    assert act_score >= 70
    assert wrong_score < act_score


def _paid_cash_flow(db_session, user_factory):
    organization = Organization(name="Synthetic finance organization")
    user = user_factory()
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic finance project", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    item = CashFlowEntry(
        project_id=project.id,
        direction="outflow",
        title="Synthetic paid invoice",
        planned_date=date(2026, 9, 10),
        planned_amount=Decimal("75000"),
        actual_date=date(2026, 9, 11),
        actual_amount=Decimal("74250"),
        status="paid",
    )
    db_session.add(item)
    db_session.flush()
    return user, item


def test_reconfirm_payment_is_idempotent_only_for_the_same_fact(db_session, user_factory, monkeypatch):
    user, item = _paid_cash_flow(db_session, user_factory)
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)

    result = confirm_payment(
        item.id,
        PaymentConfirmation(actual_amount="74250", actual_date="2026-09-11"),
        db_session,
        user,
    )

    assert result == {"id": item.id, "status": "paid", "already_confirmed": True}


@pytest.mark.parametrize(
    "payload",
    [
        PaymentConfirmation(actual_amount="75000", actual_date="2026-09-11"),
        PaymentConfirmation(actual_amount="74250", actual_date="2026-09-12"),
    ],
)
def test_reconfirm_payment_rejects_a_conflicting_fact(db_session, user_factory, monkeypatch, payload):
    user, item = _paid_cash_flow(db_session, user_factory)
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        confirm_payment(item.id, payload, db_session, user)

    assert error.value.status_code == 409
    assert "корректиров" in str(error.value.detail).casefold()


def test_payment_correction_is_an_explicit_audited_event(db_session, user_factory, monkeypatch):
    user, item = _paid_cash_flow(db_session, user_factory)
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)

    result = correct_payment(
        item.id,
        PaymentCorrection(
            expected_actual_amount="74250",
            expected_actual_date="2026-09-11",
            actual_amount="74000",
            actual_date="2026-09-12",
            reason="Исправлена подтверждённая пользователем дата оплаты",
        ),
        db_session,
        user,
    )

    assert result["status"] == "paid"
    assert result["actual_amount"] == Decimal("74000")
    assert result["actual_date"] == date(2026, 9, 12)
    assert result["corrected"] is True
    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "cash_flow_payment_corrected", AuditLog.entity_id == item.id)
        .order_by(AuditLog.id.desc())
    )
    assert audit is not None
    assert "old_amount=74250" in audit.details
    assert "new_amount=74000" in audit.details


def test_payment_correction_rejects_stale_expected_fact(db_session, user_factory, monkeypatch):
    user, item = _paid_cash_flow(db_session, user_factory)
    monkeypatch.setattr(execution_finance, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        correct_payment(
            item.id,
            PaymentCorrection(
                expected_actual_amount="75000",
                expected_actual_date="2026-09-11",
                actual_amount="74000",
                actual_date="2026-09-12",
                reason="Synthetic stale correction",
            ),
            db_session,
            user,
        )

    assert error.value.status_code == 409
    assert "измен" in str(error.value.detail).casefold()
