from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.governance import Decision, Risk
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.user import User
from app.models.document import Document

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/project")
def project_dashboard(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id)).all())
    risks = list(db.scalars(select(Risk).where(Risk.project_id == project_id)).all())
    decisions = list(db.scalars(select(Decision).where(Decision.project_id == project_id)).all())
    drafts = list(db.scalars(select(ResponseDraft).where(ResponseDraft.project_id == project_id)).all())
    registered_documents = list(db.scalars(select(Document).where(Document.project_id == project_id)).all())
    today = date.today()
    open_tasks = [x for x in tasks if x.status in {"assigned", "in_progress"}]
    overdue = [x for x in open_tasks if x.due_date and x.due_date < today]
    open_risks = [x for x in risks if x.status in {"needs_confirmation", "confirmed", "mitigating"}]
    pending_decisions = [x for x in decisions if x.status in {"needs_confirmation", "confirmed", "decided"}]

    document_map: dict[tuple[str, str], dict] = defaultdict(lambda: {"tasks": 0, "risks": 0, "decisions": 0, "drafts": 0})
    for item in tasks:
        key = (item.source_file_id, item.source_file_name)
        document_map[key]["tasks"] += 1
    for item in risks:
        key = (item.source_id, item.source_name)
        document_map[key]["risks"] += 1
    for item in decisions:
        key = (item.source_id, item.source_name)
        document_map[key]["decisions"] += 1
    for item in drafts:
        key = (item.source_file_id, item.source_file_name)
        document_map[key]["drafts"] += 1
    document_id_by_source = {item.external_id: item.id for item in registered_documents}
    for item in registered_documents:
        document_map[(item.external_id or f"db:{item.id}", item.name)]
    documents = [
        {"source_id": source_id, "document_id": document_id_by_source.get(source_id), "name": name, **counts, "attention": counts["risks"] + counts["decisions"]}
        for (source_id, name), counts in document_map.items()
    ]
    documents.sort(key=lambda x: (x["attention"], x["tasks"], x["name"]), reverse=True)
    attention = len(overdue) + len(open_risks) + len(pending_decisions)
    return {
        "summary": {
            "attention": attention,
            "open_tasks": len(open_tasks),
            "overdue_tasks": len(overdue),
            "open_risks": len(open_risks),
            "pending_decisions": len(pending_decisions),
            "drafts": len([x for x in drafts if x.status == "draft"]),
            "documents": len(documents),
        },
        "documents": documents[:100],
    }
