from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.governance_engine import create_governance_items
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.governance import Risk
from app.models.user import User
from app.organizer_engine.types import DriveFile
from app.response_engine import create_response_drafts
from app.summary_engine import brief_summary
from app.task_engine import create_tasks_from_files

router = APIRouter(prefix="/ai-secretary", tags=["ai-secretary"])


class IncomingMessage(BaseModel):
    project_id: int
    source_type: str = Field(default="manual", pattern="^(manual|email|telegram|document)$")
    source_external_id: str | None = Field(default=None, max_length=500)
    source_name: str = Field(min_length=1, max_length=1000)
    source_url: str | None = Field(default=None, max_length=2000)
    content: str = Field(min_length=1, max_length=100000)


class ContextConfirmation(BaseModel):
    contract_id: int | None = None


def _contract_candidate(db: Session, project_id: int, content: str) -> tuple[Contract | None, float, str]:
    rows = list(db.scalars(select(Contract).where(Contract.project_id == project_id, Contract.status.in_(("draft", "active")))))
    matched = [row for row in rows if row.number.casefold() in content.casefold() or (row.counterparty and row.counterparty.casefold() in content.casefold())]
    if len(matched) == 1:
        return matched[0], 0.95, f"Найден номер договора или контрагент: {matched[0].number}"
    if len(matched) > 1:
        return None, 0.45, "Найдено несколько возможных договоров; требуется подтверждение"
    return None, 0.70, "Проект выбран пользователем; договор в тексте не определён"


def _message_payload(db: Session, row: Message) -> dict:
    tasks = list(db.scalars(select(Task).where(Task.message_id == row.id).order_by(Task.id)))
    drafts = list(db.scalars(select(ResponseDraft).where(ResponseDraft.message_id == row.id).order_by(ResponseDraft.id)))
    risks = list(db.scalars(select(Risk).where(Risk.project_id == row.project_id, Risk.source_id == f"message:{row.id}").order_by(Risk.id)))
    return {
        "id": row.id, "project_id": row.project_id, "contract_id": row.contract_id,
        "source_type": row.source_type, "source_external_id": row.source_external_id,
        "source_name": row.source_name, "source_url": row.source_url,
        "summary": row.summary, "context_confidence": row.context_confidence,
        "context_evidence": row.context_evidence, "context_confirmed": row.context_confirmed,
        "status": row.status, "created_at": row.created_at,
        "tasks": [{"id": task.id, "title": task.title, "due_date": task.due_date, "confidence": task.confidence,
                   "external_action_status": task.external_action_status, "google_task_id": task.google_task_id,
                   "google_calendar_event_id": task.google_calendar_event_id} for task in tasks],
        "drafts": [{"id": draft.id, "subject": draft.subject, "body": draft.body,
                    "status": draft.status, "confidence": draft.confidence} for draft in drafts],
        "risks": [{"id": risk.id, "title": risk.title, "criticality": risk.criticality,
                   "status": risk.status, "confidence": risk.confidence,
                   "source_excerpt": risk.source_excerpt} for risk in risks],
    }


@router.get("/inbox")
def inbox(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = list(db.scalars(select(Message).where(Message.project_id == project_id).order_by(Message.created_at.desc(), Message.id.desc()).limit(200)))
    return {"messages": [_message_payload(db, row) for row in rows], "count": len(rows)}


@router.post("/inbox")
def ingest(payload: IncomingMessage, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    external_id = payload.source_external_id or f"manual:{uuid4()}"
    existing = db.scalar(select(Message).where(Message.source_type == payload.source_type, Message.source_external_id == external_id))
    if existing:
        return _message_payload(db, existing)
    contract, confidence, evidence = _contract_candidate(db, payload.project_id, payload.content)
    row = Message(
        organization_id=project.organization_id, project_id=project.id, contract_id=contract.id if contract else None,
        created_by_user_id=user.id, source_type=payload.source_type, source_external_id=external_id,
        source_name=payload.source_name.strip(), source_url=payload.source_url,
        content=payload.content.strip(), summary="Анализируется", context_confidence=confidence,
        context_evidence=evidence, context_confirmed=confidence >= 0.90,
        status="ready" if confidence >= 0.90 else "needs_context_confirmation",
    )
    db.add(row); db.flush()
    synthetic = DriveFile(id=f"message:{row.id}", name=row.source_name, mime_type="text/plain", parent_id="ai-secretary", content_text=row.content)
    tasks = create_tasks_from_files(db, row.project_id, None, [synthetic], source_type=row.source_type)
    drafts = create_response_drafts(db, row.project_id, None, [synthetic])
    risks, _ = create_governance_items(db, row.project_id, [synthetic], source_type=row.source_type)
    for task in tasks:
        task.message_id = row.id
        task.external_action_status = "proposed"
    for draft in drafts:
        draft.message_id = row.id
    row.summary = brief_summary(row.content, row.source_name, len(tasks), len(drafts), 0)
    db.add(AuditLog(action="message_processed", entity_type="message", entity_id=row.id,
                    details=f"source={row.source_type}; tasks={len(tasks)}; drafts={len(drafts)}; risks={len(risks)}; context={confidence:.0%}"))
    db.commit(); db.refresh(row)
    return _message_payload(db, row)


@router.post("/inbox/{message_id}/confirm-context")
def confirm_context(message_id: int, payload: ContextConfirmation, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(Message, message_id)
    if row is None:
        raise HTTPException(404, "Message not found")
    require_project_role(db, user, row.project_id, "editor")
    if payload.contract_id is not None:
        contract = db.scalar(select(Contract).where(Contract.id == payload.contract_id, Contract.project_id == row.project_id))
        if contract is None:
            raise HTTPException(422, "Contract does not belong to this project")
        row.contract_id = contract.id
        row.context_evidence = f"Договор подтверждён пользователем: {contract.number}"
    row.context_confirmed = True
    row.context_confidence = 1.0
    row.status = "ready"
    db.add(AuditLog(action="message_context_confirmed", entity_type="message", entity_id=row.id,
                    details=f"project={row.project_id}; contract={row.contract_id or 'none'}"))
    db.commit(); db.refresh(row)
    return _message_payload(db, row)
