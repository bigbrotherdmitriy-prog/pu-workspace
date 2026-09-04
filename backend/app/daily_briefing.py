from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_secretary import Message
from app.models.governance import Decision, Risk
from app.models.management import Obligation
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.organization_contract import Contract
from app.models.execution_finance import BudgetLine, CashFlowEntry, ScheduleBaseline, ScheduleItem


def build_daily_briefing(db: Session, project_id: int, *, today: date | None = None) -> dict:
    """Build a provider-neutral, read-only control snapshot for AI Secretary."""
    current = today or date.today()
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id)).all())
    obligations = list(db.scalars(select(Obligation).where(Obligation.project_id == project_id)).all())
    risks = list(db.scalars(select(Risk).where(Risk.project_id == project_id)).all())
    decisions = list(db.scalars(select(Decision).where(Decision.project_id == project_id)).all())
    drafts = list(db.scalars(select(ResponseDraft).where(ResponseDraft.project_id == project_id)).all())
    messages = list(db.scalars(select(Message).where(Message.project_id == project_id)).all())
    contracts = list(db.scalars(select(Contract).where(Contract.project_id == project_id)).all())
    baselines = list(db.scalars(select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id)).all())
    schedule_items = list(db.scalars(select(ScheduleItem).where(ScheduleItem.project_id == project_id)).all())
    budget_lines = list(db.scalars(select(BudgetLine).where(BudgetLine.project_id == project_id)).all())
    cash_flow = list(db.scalars(select(CashFlowEntry).where(CashFlowEntry.project_id == project_id)).all())

    attention: list[dict] = []
    open_tasks = [row for row in tasks if row.status in {"assigned", "in_progress"}]
    overdue_tasks = [row for row in open_tasks if row.due_date and row.due_date < current]
    for row in overdue_tasks:
        attention.append({
            "kind": "overdue_task", "entity_id": row.id, "priority": "critical",
            "title": row.title, "due_date": row.due_date, "source_name": row.source_file_name,
            "evidence": row.source_excerpt, "next_step": "Подтвердить исполнителя и новый срок либо завершить задачу",
        })

    open_obligations = [row for row in obligations if row.status in {"needs_confirmation", "confirmed", "in_progress"}]
    overdue_obligations = [row for row in open_obligations if row.due_date and row.due_date < current]
    for row in overdue_obligations:
        attention.append({
            "kind": "overdue_obligation", "entity_id": row.id, "priority": "critical",
            "title": row.title, "due_date": row.due_date, "source_name": row.source_name,
            "evidence": row.source_excerpt, "next_step": "Проверить исполнение обязательства и зафиксировать результат",
        })

    open_risks = [row for row in risks if row.status in {"needs_confirmation", "confirmed", "mitigating"}]
    for row in open_risks:
        attention.append({
            "kind": "risk", "entity_id": row.id,
            "priority": "high" if row.criticality in {"high", "critical"} else "normal",
            "title": row.title, "due_date": None, "source_name": row.source_name,
            "evidence": row.source_excerpt, "next_step": "Подтвердить риск и назначить действие",
        })

    pending_decisions = [row for row in decisions if row.status in {"needs_confirmation", "confirmed", "decided"}]
    for row in pending_decisions:
        attention.append({
            "kind": "decision", "entity_id": row.id, "priority": "high",
            "title": row.question, "due_date": None, "source_name": row.source_name,
            "evidence": row.source_excerpt, "next_step": "Зафиксировать решение или отклонить предложение",
        })

    waiting_drafts = [row for row in drafts if row.status == "draft"]
    for row in waiting_drafts:
        attention.append({
            "kind": "draft", "entity_id": row.id, "priority": "normal",
            "title": row.subject, "due_date": None, "source_name": row.source_file_name,
            "evidence": row.source_excerpt, "next_step": "Проверить, отредактировать и подтвердить черновик",
        })

    unconfirmed_messages = [row for row in messages if not row.context_confirmed]
    for row in unconfirmed_messages:
        attention.append({
            "kind": "context", "entity_id": row.id, "priority": "high",
            "title": row.source_name, "due_date": None, "source_name": row.source_name,
            "evidence": row.context_evidence, "next_step": "Подтвердить проект и договор сообщения",
        })

    contracts_without_source = [row for row in contracts if row.source_document_id is None]
    for row in contracts_without_source:
        attention.append({
            "kind": "missing_contract_source", "entity_id": row.id, "priority": "high",
            "title": f"Договор {row.number} без документа-источника", "due_date": None,
            "source_name": row.title, "evidence": "Юридический источник не привязан",
            "next_step": "Выбрать документ договора из реестра и запустить анализ",
        })

    baseline_ids_with_items = {row.baseline_id for row in schedule_items}
    empty_baselines = [row for row in baselines if row.id not in baseline_ids_with_items]
    for row in empty_baselines:
        attention.append({
            "kind": "empty_schedule", "entity_id": row.id, "priority": "high",
            "title": f"В ГПР «{row.name}» нет этапов", "due_date": None,
            "source_name": row.name, "evidence": "Невозможно контролировать сроки без этапов ГПР",
            "next_step": "Добавить или импортировать этапы ГПР",
        })

    unlinked_budget = [row for row in budget_lines if row.contract_id is None]
    for row in unlinked_budget:
        attention.append({
            "kind": "unlinked_budget", "entity_id": row.id, "priority": "normal",
            "title": f"Строка бюджета «{row.description}» без договора", "due_date": None,
            "source_name": row.source_name or row.category, "evidence": "Не определён договор финансового обязательства",
            "next_step": "Связать строку бюджета с договором",
        })

    unlinked_cash_flow = [row for row in cash_flow if row.contract_id is None or row.schedule_item_id is None or row.budget_line_id is None]
    for row in unlinked_cash_flow:
        attention.append({
            "kind": "unlinked_cash_flow", "entity_id": row.id, "priority": "high",
            "title": f"Запись ДДС «{row.title}» связана не полностью", "due_date": row.planned_date,
            "source_name": row.source_name or row.counterparty or "ДДС",
            "evidence": "Требуются договор, этап ГПР и строка бюджета",
            "next_step": "Проверить и заполнить связи ДДС перед подтверждением оплаты",
        })

    payment_confirmations = [
        row for row in cash_flow
        if row.status == "approved"
        and row.actual_date is None
        and row.contract_id is not None
        and row.schedule_item_id is not None
        and row.budget_line_id is not None
    ]
    for row in payment_confirmations:
        overdue = row.planned_date < current
        attention.append({
            "kind": "payment_confirmation", "entity_id": row.id,
            "priority": "critical" if overdue else "high",
            "title": f"Подтвердите факт платежа «{row.title}»",
            "due_date": row.planned_date,
            "source_name": row.source_name or row.counterparty or "ДДС",
            "evidence": (
                f"Плановая дата {row.planned_date.isoformat()}, сумма {row.planned_amount}. "
                "Банковская выписка не используется"
            ),
            "next_step": "Если платёж выполнен, вручную подтвердить дату и фактическую сумму; иначе оставить без изменений",
        })

    priority_order = {"critical": 0, "high": 1, "normal": 2}
    attention.sort(key=lambda row: (priority_order[row["priority"]], row["due_date"] or date.max, row["entity_id"]))
    summary = {
        "attention": len(attention),
        "overdue_tasks": len(overdue_tasks),
        "overdue_obligations": len(overdue_obligations),
        "open_risks": len(open_risks),
        "pending_decisions": len(pending_decisions),
        "drafts_waiting_approval": len(waiting_drafts),
        "messages_waiting_context": len(unconfirmed_messages),
        "contracts_without_source": len(contracts_without_source),
        "empty_schedules": len(empty_baselines),
        "unlinked_budget_rows": len(unlinked_budget),
        "unlinked_cash_flow": len(unlinked_cash_flow),
        "payments_waiting_confirmation": len(payment_confirmations),
    }
    return {
        "project_id": project_id,
        "date": current,
        "summary": summary,
        "attention": attention[:50],
        "next_step": attention[0]["next_step"] if attention else "Критических действий на сегодня нет",
        "external_actions_created": False,
    }
