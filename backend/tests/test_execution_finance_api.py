from datetime import date
from decimal import Decimal

from app.api.execution_finance import (
    BaselineClone,
    BaselineCreate,
    BudgetCreate,
    CashFlowCreate,
    InvoiceProposalCreate,
    PaymentConfirmation,
    ScheduleItemCreate,
    ScheduleBulkUpdate,
    StatusUpdate,
    _finance_document_hints,
    _finance_document_score,
    _linked_budget_totals,
    _remap_schedule_predecessors,
    _finish_from_start,
    _schedule_predecessors,
    _schedule_predecessor_ids,
    _start_from_finish,
    bulk_update_schedule,
    clone_baseline,
    router,
)
from app.models.execution_finance import ScheduleBaseline, ScheduleItem


def test_mvp4_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/execution/overview" in paths
    assert "/execution/document-candidates" in paths
    assert "/execution/baselines" in paths
    assert "/execution/baselines/{baseline_id}/clone" in paths
    assert "/execution/schedule-items" in paths
    assert "/execution/schedule-items/bulk" in paths
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
    schedule = ScheduleItemCreate(baseline_id=3, title="Монтаж", planned_progress=80, duration_days=12, predecessor_ids="4,5")
    budget = BudgetCreate(project_id=7, category="СМР", description="Монтаж", planned_amount="120000")
    cash = CashFlowCreate(project_id=7, direction="outflow", title="Аванс", planned_date="2026-09-01", planned_amount="50000", object_name="Дубна", category="Оборудование", note="Аванс за щиты")
    actual = StatusUpdate(status="active", actual_amount="42000", actual_date="2026-09-02")

    assert baseline.note is None
    assert baseline.contract_id == 11
    assert schedule.planned_progress == 80
    assert schedule.duration_days == 12
    assert schedule.predecessor_ids == "4,5"
    assert budget.planned_amount == Decimal("120000")
    assert cash.direction == "outflow"
    assert cash.object_name == "Дубна"
    assert cash.category == "Оборудование"
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


def test_schedule_links_accept_project_style_types_and_lags():
    assert _schedule_predecessor_ids("12FS+2д, 15SS; 21FF-1d") == [12, 15, 21]
    assert _schedule_predecessors("12FS+2д, 15SS; 21FF-1d; 4SF") == [
        (12, "FS", 2), (15, "SS", 0), (21, "FF", -1), (4, "SF", 0),
    ]


def test_schedule_links_remap_ids_without_losing_types_lags_or_formatting():
    assert _remap_schedule_predecessors(
        "12FS+2д, 15SS; 21FF-1d; 4SF",
        {12: 112, 15: 115, 21: 121, 4: 104},
    ) == "112FS+2д, 115SS; 121FF-1d; 104SF"
    assert _remap_schedule_predecessors(None, {}) is None


def test_clone_baseline_remaps_hierarchy_and_predecessors(db_session, user_factory):
    user = user_factory(is_admin=True)
    source = ScheduleBaseline(
        project_id=7, created_by_user_id=user.id, name="ГПР v1", version=1,
        status="approved", note="Исходная версия",
    )
    db_session.add(source)
    db_session.flush()
    root = ScheduleItem(
        project_id=7, baseline_id=source.id, title="Подготовка", sort_order=1,
        duration_days=3, planned_start=date(2026, 9, 1), planned_finish=date(2026, 9, 3),
    )
    db_session.add(root)
    db_session.flush()
    child = ScheduleItem(
        project_id=7, baseline_id=source.id, title="Монтаж", sort_order=2,
        parent_id=root.id, duration_days=2, predecessor_ids=f"{root.id}FS+2д",
        planned_start=date(2026, 9, 6), planned_finish=date(2026, 9, 7),
    )
    db_session.add(child)
    db_session.flush()

    result = clone_baseline(source.id, BaselineClone(name="ГПР рабочая v2"), db_session, user)

    assert result["version"] == 2
    assert result["status"] == "draft"
    assert result["source_baseline_id"] == source.id
    cloned = list(db_session.query(ScheduleItem).filter(
        ScheduleItem.baseline_id == result["id"],
    ).order_by(ScheduleItem.sort_order))
    assert len(cloned) == 2
    assert cloned[1].parent_id == cloned[0].id
    assert cloned[1].predecessor_ids == f"{cloned[0].id}FS+2д"


def test_bulk_date_shift_reschedules_successors(db_session, user_factory):
    user = user_factory(is_admin=True)
    baseline = ScheduleBaseline(
        project_id=9, created_by_user_id=user.id, name="Рабочая версия", version=1, status="draft",
    )
    db_session.add(baseline)
    db_session.flush()
    predecessor = ScheduleItem(
        project_id=9, baseline_id=baseline.id, title="Фундамент", sort_order=1,
        duration_days=3, planned_start=date(2026, 9, 1), planned_finish=date(2026, 9, 3),
    )
    db_session.add(predecessor)
    db_session.flush()
    successor = ScheduleItem(
        project_id=9, baseline_id=baseline.id, title="Каркас", sort_order=2,
        duration_days=2, predecessor_ids=f"{predecessor.id}FS",
        planned_start=date(2026, 9, 4), planned_finish=date(2026, 9, 5),
    )
    db_session.add(successor)
    db_session.flush()

    result = bulk_update_schedule(ScheduleBulkUpdate(
        baseline_id=baseline.id, item_ids=[predecessor.id], delta_days=2,
    ), db_session, user)

    assert result["updated_ids"] == [predecessor.id]
    assert successor.id in result["auto_scheduled_ids"]
    assert predecessor.planned_start == date(2026, 9, 3)
    assert predecessor.planned_finish == date(2026, 9, 5)
    assert successor.planned_start == date(2026, 9, 6)
    assert successor.planned_finish == date(2026, 9, 7)


def test_schedule_calendar_day_boundaries_are_inclusive():
    assert _finish_from_start(date(2026, 9, 1), 3) == date(2026, 9, 3)
    assert _start_from_finish(date(2026, 9, 10), 7) == date(2026, 9, 4)


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
