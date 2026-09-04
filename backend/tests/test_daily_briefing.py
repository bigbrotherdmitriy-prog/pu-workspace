from app.api.ai_secretary import router
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.daily_briefing import build_daily_briefing
from app.database import Base
from app.models.execution_finance import BudgetLine, CashFlowEntry, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract


def test_daily_briefing_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/ai-secretary/daily-briefing" in paths


def test_daily_briefing_detects_missing_contract_and_finance_links():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Contract(project_id=7, number="ГК-01", title="Договор", status="active"))
        db.add(ScheduleBaseline(project_id=7, contract_id=None, created_by_user_id=1, name="ГПР", version=1))
        db.add(BudgetLine(project_id=7, contract_id=None, category="СМР", description="Монтаж", planned_amount=Decimal("100")))
        db.add(CashFlowEntry(project_id=7, contract_id=None, schedule_item_id=None, budget_line_id=None,
                             direction="outflow", title="Счёт", planned_date=date(2026, 9, 1), planned_amount=Decimal("100")))
        db.commit()

        result = build_daily_briefing(db, 7, today=date(2026, 8, 30))

        assert result["summary"]["contracts_without_source"] == 1
        assert result["summary"]["empty_schedules"] == 1
        assert result["summary"]["unlinked_budget_rows"] == 1
        assert result["summary"]["unlinked_cash_flow"] == 1
        assert {row["kind"] for row in result["attention"]} >= {
            "missing_contract_source", "empty_schedule", "unlinked_budget", "unlinked_cash_flow",
        }
        assert result["external_actions_created"] is False


def test_daily_briefing_requires_explicit_user_confirmation_for_due_payment():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        contract = Contract(project_id=9, number="ГК-09", title="Договор", status="active")
        db.add(contract)
        db.flush()
        baseline = ScheduleBaseline(
            project_id=9, contract_id=contract.id, created_by_user_id=1,
            name="ГПР", version=1,
        )
        budget = BudgetLine(
            project_id=9, contract_id=contract.id, category="Материалы",
            description="Поставка", planned_amount=Decimal("125000"), status="approved",
        )
        db.add_all([baseline, budget])
        db.flush()
        stage = ScheduleItem(
            project_id=9, baseline_id=baseline.id, title="Поставка", status="in_progress",
        )
        db.add(stage)
        db.flush()
        db.add(CashFlowEntry(
            project_id=9, contract_id=contract.id, schedule_item_id=stage.id,
            budget_line_id=budget.id, direction="outflow", title="Счёт поставщика",
            planned_date=date(2026, 8, 29), planned_amount=Decimal("125000"),
            actual_amount=Decimal("0"), status="approved",
        ))
        db.commit()

        result = build_daily_briefing(db, 9, today=date(2026, 8, 30))

        assert result["summary"]["payments_waiting_confirmation"] == 1
        item = next(row for row in result["attention"] if row["kind"] == "payment_confirmation")
        assert item["priority"] == "critical"
        assert "вручную подтвердить" in item["next_step"]
        assert "Банковская выписка не используется" in item["evidence"]
