"""Optional bridges from the MVP-1 core to later product capabilities.

MVP-1 must remain usable without importing or initializing Tasks, response
drafts, governance, contacts, messages, or execution finance. The dedicated
MVP-1 composition installs the explicit no-op mode. The legacy application
keeps its existing behaviour unless an operator disables the bridge.

TODO(MVP-2/3): replace these lazy legacy adapters with owned interfaces and
durable handlers when the corresponding MVP is integrated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Iterable


def enabled() -> bool:
    configured = os.getenv("PU_MVP1_OPTIONAL_EXTENSIONS")
    if configured is None:
        return os.getenv("PU_MODEL_SCOPE", "").strip().lower() != "mvp1"
    return configured.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PostAnalysisResult:
    tasks: tuple[Any, ...] = field(default_factory=tuple)
    drafts: tuple[Any, ...] = field(default_factory=tuple)
    risks: tuple[Any, ...] = field(default_factory=tuple)
    decisions: tuple[Any, ...] = field(default_factory=tuple)


def run_post_analysis(
    db: Any,
    project_id: int,
    session_id: int | None,
    files: Iterable[Any],
    *,
    source_type: str | None = None,
) -> PostAnalysisResult:
    """Run later-MVP side effects only after an explicit opt-in."""
    if not enabled():
        return PostAnalysisResult()
    from app.governance_engine import create_governance_items
    from app.response_engine import create_response_drafts
    from app.task_engine import create_tasks_from_files

    materialized = list(files)
    kwargs = {"source_type": source_type} if source_type else {}
    tasks = create_tasks_from_files(db, project_id, session_id, materialized, **kwargs)
    drafts = create_response_drafts(db, project_id, session_id, materialized)
    risks, decisions = create_governance_items(db, project_id, materialized, **kwargs)
    return PostAnalysisResult(tuple(tasks), tuple(drafts), tuple(risks), tuple(decisions))


def notify(message: str) -> None:
    """Telegram notification belongs to a later communication capability."""
    if not enabled():
        return
    from app.integrations.telegram import notify_telegram

    notify_telegram(message)


def document_links(db: Any, project_id: int, source_file_id: str | None) -> dict[str, int]:
    if not enabled() or not source_file_id:
        return {"tasks": 0, "risks": 0, "decisions": 0, "drafts": 0}
    from sqlalchemy import func, select
    from app.models.governance import Decision, Risk
    from app.models.response_draft import ResponseDraft
    from app.models.task import Task

    return {
        "tasks": int(db.scalar(select(func.count(Task.id)).where(
            Task.project_id == project_id, Task.source_file_id == source_file_id,
        )) or 0),
        "risks": int(db.scalar(select(func.count(Risk.id)).where(
            Risk.project_id == project_id, Risk.source_id == source_file_id,
        )) or 0),
        "decisions": int(db.scalar(select(func.count(Decision.id)).where(
            Decision.project_id == project_id, Decision.source_id == source_file_id,
        )) or 0),
        "drafts": int(db.scalar(select(func.count(ResponseDraft.id)).where(
            ResponseDraft.project_id == project_id,
            ResponseDraft.source_file_id == source_file_id,
        )) or 0),
    }


def project_readiness_counts(db: Any, project_id: int) -> dict[str, int]:
    if not enabled():
        return {
            "schedule_rows": 0, "budget_rows": 0, "cash_flow_rows": 0,
            "contacts": 0, "confirmed_contacts": 0, "inbox_messages": 0,
        }
    from sqlalchemy import func, select
    from app.models.ai_secretary import Message
    from app.models.execution_finance import BudgetLine, CashFlowEntry, ScheduleItem
    from app.models.project_contact import ProjectContact

    count = lambda statement: int(db.scalar(statement) or 0)
    return {
        "schedule_rows": count(select(func.count(ScheduleItem.id)).where(ScheduleItem.project_id == project_id)),
        "budget_rows": count(select(func.count(BudgetLine.id)).where(BudgetLine.project_id == project_id)),
        "cash_flow_rows": count(select(func.count(CashFlowEntry.id)).where(CashFlowEntry.project_id == project_id)),
        "contacts": count(select(func.count(ProjectContact.id)).where(
            ProjectContact.project_id == project_id, ProjectContact.active.is_(True),
        )),
        "confirmed_contacts": count(select(func.count(ProjectContact.id)).where(
            ProjectContact.project_id == project_id, ProjectContact.active.is_(True),
            ProjectContact.confirmed.is_(True),
        )),
        "inbox_messages": count(select(func.count(Message.id)).where(Message.project_id == project_id)),
    }
