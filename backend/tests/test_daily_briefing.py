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
from app.models.ai_secretary import Message
from app.models.management import Obligation
from app.models.response_draft import ResponseDraft
from app.models.task import Task


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


def test_daily_briefing_does_not_duplicate_task_as_linked_obligation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = Task(
            project_id=11, assignee_user_id=1, created_by_user_id=1,
            title="Предоставить акт", status="assigned", due_date=date(2026, 8, 1),
            source_file_id="file-1", source_file_name="Договор.pdf",
            source_excerpt="Предоставить акт до 01.08.2026", source_excerpt_hash="a" * 64,
            confidence=0.9,
        )
        db.add(task)
        db.flush()
        db.add(Obligation(
            project_id=11, owner_user_id=1, task_id=task.id,
            title=task.title, status="needs_confirmation", due_date=task.due_date,
            source_type="document_analysis", source_id="file-1", source_name="Договор.pdf",
            source_excerpt=task.source_excerpt, source_hash="a" * 64, confidence=0.9,
        ))
        db.commit()

        result = build_daily_briefing(db, 11, today=date(2026, 8, 30))

        assert result["summary"]["overdue_tasks"] == 1
        assert result["summary"]["overdue_obligations"] == 0
        assert [row["kind"] for row in result["attention"]] == ["overdue_task"]


def test_daily_briefing_collapses_same_task_from_duplicate_source_name():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for index, source_name in enumerate(("Приложение №1ТЗ..docx", "Приложение №1ТЗ._.docx"), 1):
            db.add(Task(
                project_id=13, assignee_user_id=1, created_by_user_id=1,
                title="Предоставить акт", status="assigned", due_date=date(2026, 8, 1),
                source_file_id=f"file-{index}", source_file_name=source_name,
                source_excerpt="Предоставить акт до 01.08.2026", source_excerpt_hash=str(index) * 64,
                confidence=0.9,
            ))
        db.commit()

        result = build_daily_briefing(db, 13, today=date(2026, 8, 30))

        assert result["summary"]["overdue_tasks"] == 1
        assert len([row for row in result["attention"] if row["kind"] == "overdue_task"]) == 1


def test_daily_briefing_suppresses_legacy_reference_date_without_deleting_entities():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = Task(
            project_id=14, assignee_user_id=1, created_by_user_id=1,
            title="Соблюдать требования закона", status="assigned", due_date=date(2006, 7, 27),
            source_file_id="law-1", source_file_name="Договор.pdf",
            source_excerpt="Исполнитель обязан соблюдать Федеральный закон от 27.07.2006 № 152-ФЗ",
            source_excerpt_hash="b" * 64, confidence=0.9, needs_review=True,
        )
        db.add(task)
        db.flush()
        obligation = Obligation(
            project_id=14, owner_user_id=1, task_id=task.id,
            title=task.title, status="needs_confirmation", due_date=task.due_date,
            source_type="document_analysis", source_id="law-1", source_name="Договор.pdf",
            source_excerpt=task.source_excerpt, source_hash="b" * 64, confidence=0.9,
        )
        db.add(obligation)
        db.commit()

        result = build_daily_briefing(db, 14, today=date(2026, 8, 30))

        assert result["summary"]["overdue_tasks"] == 0
        assert result["summary"]["overdue_obligations"] == 0
        assert db.get(Task, task.id) is not None
        assert db.get(Obligation, obligation.id) is not None


def test_daily_briefing_excludes_filtered_message_from_context_attention():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Message(
                organization_id=1, project_id=12, created_by_user_id=1,
                source_type="email", source_external_id="filtered-1",
                source_name="Рассылка", content="Рекламное письмо", summary="Отфильтровано",
                context_confidence=0.0, context_evidence="Категория Промоакции",
                context_confirmed=False, status="filtered",
            ),
            Message(
                organization_id=1, project_id=12, created_by_user_id=1,
                source_type="email", source_external_id="business-1",
                source_name="Письмо заказчика", content="Просим направить акт", summary="Анализ",
                context_confidence=0.5, context_evidence="Недостаточно признаков",
                context_confirmed=False, status="needs_context_confirmation",
            ),
        ])
        db.commit()

        result = build_daily_briefing(db, 12, today=date(2026, 8, 30))

        assert result["summary"]["messages_waiting_context"] == 1
        contexts = [row for row in result["attention"] if row["kind"] == "context"]
        assert [row["title"] for row in contexts] == ["Письмо заказчика"]


def test_daily_briefing_excludes_stale_draft_linked_to_filtered_message():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        message = Message(
            organization_id=1, project_id=15, created_by_user_id=1,
            source_type="email", source_external_id="machine-1",
            source_name="Служебное письмо", content="Автоматическое уведомление",
            summary="Отфильтровано", context_confidence=0.0,
            context_evidence="Служебный отправитель", context_confirmed=False,
            status="filtered",
        )
        db.add(message)
        db.flush()
        db.add(ResponseDraft(
            project_id=15, reviewer_user_id=1, message_id=message.id,
            subject="Re: уведомление", body="Черновик не должен требовать внимания",
            status="draft", source_file_id=f"message:{message.id}",
            source_file_name=message.source_name, source_excerpt=message.content,
            source_excerpt_hash="c" * 64, confidence=0.5,
        ))
        db.commit()

        result = build_daily_briefing(db, 15, today=date(2026, 8, 30))

        assert result["summary"]["drafts_waiting_approval"] == 0
        assert not any(row["kind"] == "draft" for row in result["attention"])
