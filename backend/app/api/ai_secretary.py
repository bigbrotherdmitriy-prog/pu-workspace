from __future__ import annotations

from uuid import uuid4
from datetime import date
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.governance_engine import create_governance_items
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.governance import Risk
from app.models.user import User
from app.models.automation_rule import AutomationRule, AutomationRun
from app.automation_engine import next_monthly_date, prepare_rule_run
from app.core.integration_types import StorageObject
from app.response_engine import create_response_drafts
from app.summary_engine import brief_summary
from app.task_engine import create_tasks_from_files
from app.integrations.external_resources import external_id_for

router = APIRouter(prefix="/ai-secretary", tags=["ai-secretary"])


class IncomingMessage(BaseModel):
    project_id: int
    source_type: str = Field(default="manual", pattern="^(manual|email|telegram|document)$")
    source_external_id: str | None = Field(default=None, max_length=500)
    source_name: str = Field(min_length=1, max_length=1000)
    source_url: str | None = Field(default=None, max_length=2000)
    source_sender: str | None = Field(default=None, max_length=1000)
    source_thread_id: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1, max_length=100000)
    attachments: list[dict] = Field(default_factory=list)
    routing_contract_id: int | None = None
    routing_evidence: str | None = Field(default=None, max_length=1000)


class ContextConfirmation(BaseModel):
    project_id: int | None = None
    contract_id: int | None = None


class BulkContextConfirmation(ContextConfirmation):
    message_ids: list[int] = Field(min_length=1, max_length=200)


class MessageStatusUpdate(BaseModel):
    status: str = Field(pattern="^(ready|needs_context_confirmation|in_progress|completed)$")


class AutomationRuleCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    source_document_id: int | None = None
    name: str = Field(min_length=1, max_length=500)
    day_of_month: int = Field(ge=1, le=31)
    recipient_to: str = Field(min_length=3, max_length=1000)
    subject_template: str = Field(min_length=1, max_length=500)
    body_template: str = Field(min_length=1, max_length=20000)
    task_title_template: str = Field(min_length=1, max_length=500)


class AutomationRuleState(BaseModel):
    active: bool


def _contract_candidate(db: Session, project_id: int, content: str) -> tuple[Contract | None, float, str]:
    rows = list(db.scalars(select(Contract).where(Contract.project_id == project_id, Contract.status.in_(("draft", "active")))))
    matched = [row for row in rows if row.number.casefold() in content.casefold() or (row.counterparty and row.counterparty.casefold() in content.casefold())]
    if len(matched) == 1:
        return matched[0], 0.95, f"Найден номер договора или контрагент: {matched[0].number}"
    if len(matched) > 1:
        return None, 0.45, "Найдено несколько возможных договоров; требуется подтверждение"
    return None, 0.70, "Проект выбран пользователем; договор в тексте не определён"


def project_candidate(db: Session, fallback_project_id: int, content: str, user: User | None = None) -> tuple[int, float, str]:
    """Select a project from explicit names or contract evidence, otherwise keep a reviewable fallback."""
    fallback = db.get(Project, fallback_project_id)
    if fallback is None:
        raise HTTPException(404, "Project not found")
    text_value = content.casefold()
    project_query = select(Project).where(Project.organization_id == fallback.organization_id)
    if user is not None and not user.is_admin:
        project_query = project_query.join(ProjectMember, ProjectMember.project_id == Project.id).where(
            ProjectMember.user_id == user.id,
        )
    projects = list(db.scalars(project_query))
    allowed_project_ids = {project.id for project in projects}
    matches: dict[int, list[str]] = {}
    for project in projects:
        name = project.name.strip().casefold()
        if len(name) >= 4 and name in text_value:
            matches.setdefault(project.id, []).append(f"название проекта «{project.name}»")
    for contract in db.scalars(
        select(Contract).join(Project, Project.id == Contract.project_id).where(
            Project.organization_id == fallback.organization_id,
            Contract.project_id.in_(allowed_project_ids),
        )
    ):
        evidence = []
        if contract.number and contract.number.casefold() in text_value:
            evidence.append(f"договор {contract.number}")
        if contract.counterparty and len(contract.counterparty.strip()) >= 4 and contract.counterparty.casefold() in text_value:
            evidence.append(f"контрагент {contract.counterparty}")
        if evidence:
            matches.setdefault(contract.project_id, []).extend(evidence)
    if len(matches) == 1:
        project_id, evidence = next(iter(matches.items()))
        return project_id, 0.95, "Проект определён по содержанию: " + ", ".join(evidence[:3])
    if len(matches) > 1:
        return fallback_project_id, 0.40, "Найдено несколько возможных проектов; требуется подтверждение"
    return fallback_project_id, 0.55, "Проект по содержанию не определён; требуется подтверждение"


def _message_payload(db: Session, row: Message) -> dict:
    tasks = list(db.scalars(select(Task).where(Task.message_id == row.id).order_by(Task.id)))
    drafts = list(db.scalars(select(ResponseDraft).where(ResponseDraft.message_id == row.id).order_by(ResponseDraft.id)))
    risks = list(db.scalars(select(Risk).where(Risk.project_id == row.project_id, Risk.source_id == f"message:{row.id}").order_by(Risk.id)))
    attachments = json.loads(row.attachments_json or "[]")
    attachment_ids = [item["document_external_id"] for item in attachments if item.get("document_external_id")]
    imported = {
        document.external_id: document.id
        for document in db.scalars(select(Document).where(
            Document.project_id == row.project_id,
            Document.external_id.in_(attachment_ids),
        ))
    } if attachment_ids else {}
    for item in attachments:
        external_id = item.get("document_external_id")
        item["document_id"] = imported.get(external_id)
        item["imported"] = bool(item["document_id"])
    task_payloads = []
    for task in tasks:
        external_task_id = external_id_for(
            db, entity_type="task", entity_id=task.id, provider="google_workspace",
            resource_type="task", legacy_id=task.google_task_id,
        )
        external_calendar_id = external_id_for(
            db, entity_type="task", entity_id=task.id, provider="google_workspace",
            resource_type="calendar_event", legacy_id=task.google_calendar_event_id,
        )
        task_payloads.append({
            "id": task.id, "title": task.title, "due_date": task.due_date, "confidence": task.confidence,
            "external_action_status": task.external_action_status, "google_task_id": external_task_id,
            "google_calendar_event_id": external_calendar_id,
            "external_resources": [
                *([{"provider": "google_workspace", "resource_type": "task", "external_id": external_task_id}] if external_task_id else []),
                *([{"provider": "google_workspace", "resource_type": "calendar_event", "external_id": external_calendar_id}] if external_calendar_id else []),
            ],
        })
    return {
        "id": row.id, "project_id": row.project_id, "contract_id": row.contract_id,
        "source_type": row.source_type, "source_external_id": row.source_external_id,
        "source_name": row.source_name, "source_url": row.source_url,
        "source_sender": row.source_sender, "source_thread_id": row.source_thread_id,
        "content": row.content, "attachments": attachments,
        "summary": row.summary, "context_confidence": row.context_confidence,
        "context_evidence": row.context_evidence, "context_confirmed": row.context_confirmed,
        "status": row.status, "created_at": row.created_at,
        "tasks": task_payloads,
        "drafts": [{"id": draft.id, "subject": draft.subject, "body": draft.body,
                    "status": draft.status, "confidence": draft.confidence} for draft in drafts],
        "risks": [{"id": risk.id, "title": risk.title, "criticality": risk.criticality,
                   "status": risk.status, "confidence": risk.confidence,
                   "source_excerpt": risk.source_excerpt} for risk in risks],
    }


@router.get("/inbox")
def inbox(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    project = db.get(Project, project_id)
    if user.is_admin:
        accessible_project_ids = list(db.scalars(select(Project.id).where(Project.organization_id == project.organization_id)))
    else:
        accessible_project_ids = list(db.scalars(
            select(ProjectMember.project_id).join(Project, Project.id == ProjectMember.project_id).where(
                ProjectMember.user_id == user.id,
                Project.organization_id == project.organization_id,
            )
        ))
    rows = list(db.scalars(select(Message).where(
        Message.organization_id == project.organization_id,
        Message.project_id.in_(accessible_project_ids),
        (Message.project_id == project_id) | (Message.context_confirmed.is_(False)),
    ).order_by(Message.created_at.desc(), Message.id.desc()).limit(200)))
    return {"messages": [_message_payload(db, row) for row in rows], "count": len(rows)}


@router.patch("/inbox/{message_id}/status")
def update_message_status(message_id: int, payload: MessageStatusUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(Message, message_id)
    if row is None:
        raise HTTPException(404, "Message not found")
    require_project_role(db, user, row.project_id, "editor")
    row.status = payload.status
    db.add(AuditLog(action="message_status_updated", entity_type="message", entity_id=row.id,
                    details=f"status={payload.status}"))
    db.commit(); db.refresh(row)
    return _message_payload(db, row)


def ingest_message(payload: IncomingMessage, db: Session, user: User) -> dict:
    require_project_role(db, user, payload.project_id, "editor")
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    external_id = payload.source_external_id or f"manual:{uuid4()}"
    existing = db.scalar(select(Message).where(Message.source_type == payload.source_type, Message.source_external_id == external_id))
    if existing:
        return _message_payload(db, existing)
    if payload.routing_contract_id is not None:
        contract = db.get(Contract, payload.routing_contract_id)
        if contract is None or contract.project_id != payload.project_id:
            raise HTTPException(422, "Контакт связан с договором другого проекта")
        confidence, evidence = 0.99, payload.routing_evidence or "Проект и договор определены по email клиента"
    else:
        contract, confidence, evidence = _contract_candidate(db, payload.project_id, payload.content)
        if payload.routing_evidence:
            confidence, evidence = max(confidence, 0.99), payload.routing_evidence
    row = Message(
        organization_id=project.organization_id, project_id=project.id, contract_id=contract.id if contract else None,
        created_by_user_id=user.id, source_type=payload.source_type, source_external_id=external_id,
        source_name=payload.source_name.strip(), source_url=payload.source_url,
        source_sender=payload.source_sender, source_thread_id=payload.source_thread_id,
        content=payload.content.strip(), attachments_json=json.dumps(payload.attachments, ensure_ascii=False),
        summary="Анализируется", context_confidence=confidence,
        context_evidence=evidence, context_confirmed=confidence >= 0.90,
        status="ready" if confidence >= 0.90 else "needs_context_confirmation",
    )
    db.add(row); db.flush()
    synthetic = StorageObject(id=f"message:{row.id}", name=row.source_name, mime_type="text/plain", parent_id="ai-secretary", content_text=row.content)
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


@router.post("/inbox")
def ingest(payload: IncomingMessage, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return ingest_message(payload, db, user)


@router.post("/inbox/{message_id}/confirm-context")
def confirm_context(message_id: int, payload: ContextConfirmation, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(Message, message_id)
    if row is None:
        raise HTTPException(404, "Message not found")
    require_project_role(db, user, row.project_id, "viewer")
    target_project_id = payload.project_id or row.project_id
    require_project_role(db, user, target_project_id, "editor")
    target_project = db.get(Project, target_project_id)
    if target_project is None or target_project.organization_id != row.organization_id:
        raise HTTPException(422, "Project does not belong to this organization")
    if target_project_id != row.project_id:
        old_project_id = row.project_id
        row.project_id = target_project_id
        row.contract_id = None
        for task in db.scalars(select(Task).where(Task.message_id == row.id)):
            task.project_id = target_project_id
        for draft in db.scalars(select(ResponseDraft).where(ResponseDraft.message_id == row.id)):
            draft.project_id = target_project_id
        for risk in db.scalars(select(Risk).where(Risk.project_id == old_project_id, Risk.source_id == f"message:{row.id}")):
            risk.project_id = target_project_id
    if payload.contract_id is not None:
        contract = db.scalar(select(Contract).where(Contract.id == payload.contract_id, Contract.project_id == target_project_id))
        if contract is None:
            raise HTTPException(422, "Contract does not belong to this project")
        row.contract_id = contract.id
        row.context_evidence = f"Проект и договор подтверждены пользователем: {target_project.name}; {contract.number}"
    else:
        row.context_evidence = f"Проект подтверждён пользователем: {target_project.name}"
    row.context_confirmed = True
    row.context_confidence = 1.0
    row.status = "ready"
    db.add(AuditLog(action="message_context_confirmed", entity_type="message", entity_id=row.id,
                    details=f"project={row.project_id}; contract={row.contract_id or 'none'}"))
    db.commit(); db.refresh(row)
    return _message_payload(db, row)


@router.post("/inbox/confirm-context-bulk")
def confirm_context_bulk(payload: BulkContextConfirmation, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Atomically move and confirm several messages in one user-approved action."""
    message_ids = list(dict.fromkeys(payload.message_ids))
    rows = list(db.scalars(select(Message).where(Message.id.in_(message_ids)).order_by(Message.id)))
    if len(rows) != len(message_ids):
        raise HTTPException(404, "One or more messages were not found")
    target_project_id = payload.project_id
    if target_project_id is None:
        raise HTTPException(422, "Target project is required")
    require_project_role(db, user, target_project_id, "editor")
    target_project = db.get(Project, target_project_id)
    if target_project is None:
        raise HTTPException(404, "Project not found")
    contract = None
    if payload.contract_id is not None:
        contract = db.scalar(select(Contract).where(
            Contract.id == payload.contract_id,
            Contract.project_id == target_project_id,
        ))
        if contract is None:
            raise HTTPException(422, "Contract does not belong to this project")
    for row in rows:
        require_project_role(db, user, row.project_id, "viewer")
        if row.organization_id != target_project.organization_id:
            raise HTTPException(422, "Messages and target project must belong to the same organization")
    moved = 0
    for row in rows:
        old_project_id = row.project_id
        if old_project_id != target_project_id:
            moved += 1
            row.project_id = target_project_id
            for task in db.scalars(select(Task).where(Task.message_id == row.id)):
                task.project_id = target_project_id
            for draft in db.scalars(select(ResponseDraft).where(ResponseDraft.message_id == row.id)):
                draft.project_id = target_project_id
            for risk in db.scalars(select(Risk).where(
                Risk.project_id == old_project_id,
                Risk.source_id == f"message:{row.id}",
            )):
                risk.project_id = target_project_id
        row.contract_id = contract.id if contract else None
        row.context_confirmed = True
        row.context_confidence = 1.0
        row.status = "ready"
        row.context_evidence = (
            f"Массово подтверждены проект и договор: {target_project.name}; {contract.number}"
            if contract else f"Массово подтверждён проект: {target_project.name}"
        )
    db.add(AuditLog(
        action="message_context_bulk_confirmed",
        entity_type="project",
        entity_id=target_project_id,
        details=f"messages={len(rows)}; moved={moved}; contract={contract.id if contract else 'none'}",
    ))
    db.commit()
    return {"confirmed": len(rows), "moved": moved, "project_id": target_project_id,
            "contract_id": contract.id if contract else None}


def _automation_rule_payload(db: Session, row: AutomationRule) -> dict:
    runs = list(db.scalars(select(AutomationRun).where(
        AutomationRun.rule_id == row.id,
    ).order_by(AutomationRun.scheduled_for.desc()).limit(12)))
    return {
        "id": row.id, "project_id": row.project_id, "contract_id": row.contract_id,
        "source_document_id": row.source_document_id, "name": row.name, "kind": row.kind,
        "day_of_month": row.day_of_month, "recipient_to": row.recipient_to,
        "subject_template": row.subject_template, "body_template": row.body_template,
        "task_title_template": row.task_title_template, "active": row.active,
        "next_run_on": row.next_run_on, "last_run_on": row.last_run_on,
        "runs": [{"id": run.id, "scheduled_for": run.scheduled_for, "task_id": run.task_id,
                  "response_draft_id": run.response_draft_id, "status": run.status} for run in runs],
    }


@router.get("/automations")
def list_automation_rules(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = list(db.scalars(select(AutomationRule).where(
        AutomationRule.project_id == project_id,
    ).order_by(AutomationRule.active.desc(), AutomationRule.next_run_on, AutomationRule.id)))
    return {"rules": [_automation_rule_payload(db, row) for row in rows], "count": len(rows)}


@router.post("/automations")
def create_automation_rule(payload: AutomationRuleCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
    if "@" not in payload.recipient_to or payload.recipient_to.startswith("@"):
        raise HTTPException(422, "Укажите корректный адрес получателя")
    if payload.contract_id is not None and not db.scalar(select(Contract.id).where(
        Contract.id == payload.contract_id, Contract.project_id == payload.project_id,
    )):
        raise HTTPException(422, "Contract does not belong to this project")
    if payload.source_document_id is not None and not db.scalar(select(Document.id).where(
        Document.id == payload.source_document_id, Document.project_id == payload.project_id,
    )):
        raise HTTPException(422, "Document does not belong to this project")
    row = AutomationRule(
        **payload.model_dump(), created_by_user_id=user.id,
        next_run_on=next_monthly_date(payload.day_of_month, date.today()),
    )
    db.add(row); db.flush()
    db.add(AuditLog(action="automation_rule_created", entity_type="automation_rule", entity_id=row.id,
                    details=f"project={row.project_id}; day={row.day_of_month}; confirmation_required=true"))
    db.commit(); db.refresh(row)
    return _automation_rule_payload(db, row)


@router.patch("/automations/{rule_id}")
def update_automation_rule(rule_id: int, payload: AutomationRuleState,
                           db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(AutomationRule, rule_id)
    if row is None:
        raise HTTPException(404, "Automation rule not found")
    require_project_role(db, user, row.project_id, "manager")
    row.active = payload.active
    db.add(AuditLog(action="automation_rule_state_changed", entity_type="automation_rule", entity_id=row.id,
                    details=f"active={row.active}"))
    db.commit(); db.refresh(row)
    return _automation_rule_payload(db, row)


@router.post("/automations/{rule_id}/run-now")
def run_automation_rule_now(rule_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(AutomationRule, rule_id)
    if row is None:
        raise HTTPException(404, "Automation rule not found")
    require_project_role(db, user, row.project_id, "manager")
    run = prepare_rule_run(db, row, date.today())
    db.add(AuditLog(action="automation_rule_run_prepared", entity_type="automation_run", entity_id=run.id,
                    details=f"rule={row.id}; task={run.task_id}; draft={run.response_draft_id}; sent=false"))
    db.commit()
    return {"run_id": run.id, "task_id": run.task_id, "response_draft_id": run.response_draft_id,
            "status": run.status, "sent": False}
