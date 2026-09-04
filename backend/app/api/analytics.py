from collections import Counter
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.ai_secretary import Message
from app.models.document import Document
from app.models.governance import Decision, Risk
from app.models.organization_contract import Contract
from app.models.task import Task
from app.models.user import User

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _distribution(values: list[str | None], fallback: str = "unknown") -> list[dict]:
    counts = Counter(value or fallback for value in values)
    return [
        {"key": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


@router.get("/project")
def project_analytics(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Provider-neutral operational analytics built only from PU Workspace Core."""
    require_project_role(db, user, project_id, "viewer")
    documents = list(db.scalars(select(Document).where(Document.project_id == project_id)))
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id)))
    risks = list(db.scalars(select(Risk).where(Risk.project_id == project_id)))
    decisions = list(db.scalars(select(Decision).where(Decision.project_id == project_id)))
    contracts = list(db.scalars(select(Contract).where(Contract.project_id == project_id)))
    messages = list(db.scalars(select(Message).where(Message.project_id == project_id)))

    today = date.today()
    open_tasks = [item for item in tasks if item.status in {"assigned", "in_progress"}]
    overdue_tasks = [item for item in open_tasks if item.due_date and item.due_date < today]
    open_risks = [item for item in risks if item.status not in {"resolved", "dismissed"}]
    pending_decisions = [item for item in decisions if item.status not in {"executed", "dismissed"}]
    pending_messages = [item for item in messages if item.status != "completed" or not item.context_confirmed]

    return {
        "summary": {
            "documents": len(documents),
            "document_coverage": round(100 * sum(bool(item.summary) for item in documents) / len(documents)) if documents else 0,
            "open_tasks": len(open_tasks),
            "overdue_tasks": len(overdue_tasks),
            "open_risks": len(open_risks),
            "pending_decisions": len(pending_decisions),
            "contracts": len(contracts),
            "active_contracts": sum(item.status == "active" for item in contracts),
            "messages": len(messages),
            "pending_messages": len(pending_messages),
        },
        "documents_by_source": _distribution([item.source for item in documents]),
        "documents_by_status": _distribution([item.status for item in documents]),
        "tasks_by_status": _distribution([item.status for item in tasks]),
        "risks_by_criticality": _distribution([item.criticality for item in open_risks]),
        "messages_by_channel": _distribution([item.source_type for item in messages]),
    }
