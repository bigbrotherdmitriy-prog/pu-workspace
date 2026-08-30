from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_secretary import Message
from app.models.governance import Decision, Risk
from app.models.management import Obligation
from app.models.response_draft import ResponseDraft
from app.models.task import Task


def build_daily_briefing(db: Session, project_id: int, *, today: date | None = None) -> dict:
    """Build a provider-neutral, read-only control snapshot for AI Secretary."""
    current = today or date.today()
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id)).all())
    obligations = list(db.scalars(select(Obligation).where(Obligation.project_id == project_id)).all())
    risks = list(db.scalars(select(Risk).where(Risk.project_id == project_id)).all())
    decisions = list(db.scalars(select(Decision).where(Decision.project_id == project_id)).all())
    drafts = list(db.scalars(select(ResponseDraft).where(ResponseDraft.project_id == project_id)).all())
    messages = list(db.scalars(select(Message).where(Message.project_id == project_id)).all())

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
    }
    return {
        "project_id": project_id,
        "date": current,
        "summary": summary,
        "attention": attention[:50],
        "next_step": attention[0]["next_step"] if attention else "Критических действий на сегодня нет",
        "external_actions_created": False,
    }
