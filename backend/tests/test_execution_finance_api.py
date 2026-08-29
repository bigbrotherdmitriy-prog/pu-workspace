from decimal import Decimal

from app.api.execution_finance import (
    BaselineCreate,
    BudgetCreate,
    CashFlowCreate,
    InvoiceProposalCreate,
    PaymentConfirmation,
    ScheduleItemCreate,
    StatusUpdate,
    _finance_document_hints,
    _finance_document_score,
    _linked_budget_totals,
    router,
)


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
