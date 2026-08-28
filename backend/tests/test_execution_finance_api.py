from decimal import Decimal

from app.api.execution_finance import (
    BaselineCreate,
    BudgetCreate,
    CashFlowCreate,
    InvoiceProposalCreate,
    PaymentConfirmation,
    ScheduleItemCreate,
    StatusUpdate,
    router,
)


def test_mvp4_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/execution/overview" in paths
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
