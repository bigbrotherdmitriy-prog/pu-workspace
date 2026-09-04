from __future__ import annotations

from uuid import uuid4
from datetime import date, datetime, timezone
import json
import re

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
from app.models.task import TaskHistory
from app.models.task_completion_suggestion import TaskCompletionSuggestion
from app.models.governance import Risk
from app.models.user import User
from app.models.v54_pilot import DeadlineClaim
from app.core.v54_refs import VersionPin
from app.models.automation_rule import AutomationRule, AutomationRun
from app.automation_engine import next_monthly_date, prepare_rule_run
from app.core.integration_types import StorageObject
from app.response_engine import create_response_drafts
from app.summary_engine import brief_summary
from app.task_engine import create_tasks_from_files
from app.integrations.external_resources import external_id_for
from app.integrations.actions import configured_action_adapter
from app.daily_briefing import build_daily_briefing
from app.provider_actions.email_compensation import (
    describe_email_compensation,
    unavailable_email_compensation,
)

router = APIRouter(prefix="/ai-secretary", tags=["ai-secretary"])


class IncomingMessage(BaseModel):
    project_id: int
    source_type: str = Field(default="manual", pattern="^(manual|email|email_outgoing|telegram|document)$")
    source_external_id: str | None = Field(default=None, max_length=500)
    source_name: str = Field(min_length=1, max_length=1000)
    source_url: str | None = Field(default=None, max_length=2000)
    source_sender: str | None = Field(default=None, max_length=1000)
    source_thread_id: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1, max_length=100000)
    attachments: list[dict] = Field(default_factory=list)
    routing_contract_id: int | None = None
    routing_evidence: str | None = Field(default=None, max_length=1000)
    routing_confidence: float | None = Field(default=None, ge=0, le=1)
    automation_suppressed: bool = False
    automation_suppression_reason: str | None = Field(default=None, max_length=1000)


class ContextConfirmation(BaseModel):
    project_id: int | None = None
    contract_id: int | None = None


class BulkContextConfirmation(ContextConfirmation):
    message_ids: list[int] = Field(min_length=1, max_length=200)


class MessageStatusUpdate(BaseModel):
    status: str = Field(pattern="^(ready|needs_context_confirmation|in_progress|completed)$")


class CompletionReview(BaseModel):
    status: str = Field(pattern="^(confirmed|rejected)$")


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


@router.get("/daily-briefing")
def daily_briefing(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    return build_daily_briefing(db, project_id)


def _explicit_mail_reference(value: str | None, content: str) -> bool:
    value = (value or "").strip()
    return bool(value and re.search(r"(?<![\w@.-])" + re.escape(value) + r"(?![\w@.-])", content, re.IGNORECASE))


def _contract_candidate(db: Session, project_id: int, content: str) -> tuple[Contract | None, float, str]:
    rows = list(db.scalars(select(Contract).where(Contract.project_id == project_id, Contract.status.in_(("draft", "active")))))
    matched = [row for row in rows if _explicit_mail_reference(row.number, content)]
    if len(matched) == 1:
        return matched[0], 0.95, f"Найден номер договора: {matched[0].number}"
    if len(matched) > 1:
        return None, 0.45, "Найдено несколько возможных договоров; требуется подтверждение"
    return None, 0.70, "Проект выбран пользователем; договор в тексте не определён"


def project_candidate(db: Session, fallback_project_id: int, content: str, user: User | None = None) -> tuple[int, float, str]:
    """Select a project from explicit names or contract evidence, otherwise keep a reviewable fallback."""
    fallback = db.get(Project, fallback_project_id)
    if fallback is None:
        raise HTTPException(404, "Project not found")
    text_value = content.casefold()
    project_query = select(Project).where(Project.organization_id == fallback.organization_id, Project.archived_at.is_(None))
    if user is not None and not user.is_admin:
        project_query = project_query.join(ProjectMember, ProjectMember.project_id == Project.id).where(
            ProjectMember.user_id == user.id,
        )
    projects = list(db.scalars(project_query))
    allowed_project_ids = {project.id for project in projects}
    matches: dict[int, list[str]] = {}
    for project in projects:
        name = project.name.strip().casefold()
        if len(name) >= 4 and _explicit_mail_reference(name, text_value):
            matches.setdefault(project.id, []).append(f"название проекта «{project.name}»")
    for contract in db.scalars(
        select(Contract).join(Project, Project.id == Contract.project_id).where(
            Project.organization_id == fallback.organization_id,
            Contract.project_id.in_(allowed_project_ids),
        )
    ):
        evidence = []
        if _explicit_mail_reference(contract.number, text_value):
            evidence.append(f"договор {contract.number}")
        if evidence:
            matches.setdefault(contract.project_id, []).extend(evidence)
    if len(matches) == 1:
        project_id, evidence = next(iter(matches.items()))
        return project_id, 0.95, "Проект определён по содержанию: " + ", ".join(evidence[:3])
    if len(matches) > 1:
        return fallback_project_id, 0.40, f"Кандидаты проектов: {','.join(map(str, sorted(matches)))}; требуется подтверждение"
    return fallback_project_id, 0.55, "Проект по содержанию не определён; требуется подтверждение"


def _message_payload(db: Session, row: Message, action_provider: str | None = None,
                     actor: User | None = None) -> dict:
    action_provider = action_provider or configured_action_adapter(row.project_id, db).provider
    actor_role = db.scalar(select(ProjectMember.role).where(
        ProjectMember.project_id == row.project_id,
        ProjectMember.user_id == actor.id,
    )) if actor is not None and not actor.is_admin else None
    can_prepare_external_action = bool(
        actor is not None and (actor.is_admin or actor_role in {"manager", "owner"})
    )
    tasks = list(db.scalars(select(Task).where(Task.message_id == row.id).order_by(Task.id)))
    drafts = list(db.scalars(select(ResponseDraft).where(ResponseDraft.message_id == row.id).order_by(ResponseDraft.id)))
    risks = list(db.scalars(select(Risk).where(Risk.project_id == row.project_id, Risk.source_id == f"message:{row.id}").order_by(Risk.id)))
    completion_rows = db.execute(select(TaskCompletionSuggestion, Task).join(Task, Task.id == TaskCompletionSuggestion.task_id).where(
        TaskCompletionSuggestion.message_id == row.id,
        TaskCompletionSuggestion.project_id == row.project_id,
        Task.project_id == row.project_id,
    ).order_by(TaskCompletionSuggestion.confidence.desc(), TaskCompletionSuggestion.id)).all()
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
    workflow_state, workflow_reason = _message_workflow_state(
        row, tasks=tasks, drafts=drafts, risks=risks,
        completion_suggestions=[suggestion for suggestion, _task in completion_rows],
    )
    task_payloads = []
    for task in tasks:
        external_task_id = external_id_for(
            db, entity_type="task", entity_id=task.id, provider=action_provider,
            resource_type="task", legacy_id=task.google_task_id,
        )
        external_calendar_id = external_id_for(
            db, entity_type="task", entity_id=task.id, provider=action_provider,
            resource_type="calendar_event", legacy_id=task.google_calendar_event_id,
        )
        task_payloads.append({
            "id": task.id, "title": task.title, "due_date": task.due_date, "confidence": task.confidence,
            "external_action_status": task.external_action_status, "google_task_id": external_task_id,
            "google_calendar_event_id": external_calendar_id,
            "external_resources": [
                *([{"provider": action_provider, "resource_type": "task", "external_id": external_task_id}] if external_task_id else []),
                *([{"provider": action_provider, "resource_type": "calendar_event", "external_id": external_calendar_id}] if external_calendar_id else []),
            ],
        })
    evidence_refs = []
    seen_evidence = set()
    for pins in db.scalars(select(DeadlineClaim.evidence_pins).where(
        DeadlineClaim.organization_id == row.organization_id,
        DeadlineClaim.message_id == row.id,
    ).order_by(DeadlineClaim.revision.desc())):
        if not isinstance(pins, list):
            continue
        for value in pins:
            try:
                pin = VersionPin.model_validate(value)
            except Exception:
                continue
            if (pin.ref.type != "evidence" or pin.ref.tenant_id.value != str(row.organization_id)
                    or pin.ref.id.value in seen_evidence):
                continue
            seen_evidence.add(pin.ref.id.value)
            evidence_refs.append({"id": pin.ref.id.value, "revision": pin.value})
    return {
        "id": row.id, "project_id": row.project_id, "contract_id": row.contract_id,
        "source_type": row.source_type, "source_external_id": row.source_external_id,
        "source_name": row.source_name, "source_url": row.source_url,
        "source_sender": row.source_sender, "source_thread_id": row.source_thread_id,
        "content": row.content, "attachments": attachments,
        "summary": row.summary, "context_confidence": row.context_confidence,
        "context_evidence": row.context_evidence, "context_confirmed": row.context_confirmed,
        "status": row.status, "created_at": row.created_at,
        "analysis_required": row.analysis_required,
        "workflow_state": workflow_state, "workflow_reason": workflow_reason,
        "tasks": task_payloads,
        "drafts": [{"id": draft.id, "subject": draft.subject, "body": draft.body,
                    "recipient_to": draft.recipient_to,
                    "status": draft.status, "confidence": draft.confidence,
                    "is_corrective_follow_up": draft.source_file_name == "corrective-follow-up",
                    "email_compensation": (
                        describe_email_compensation(db, draft)
                        if draft.status == "sent" and can_prepare_external_action
                        else unavailable_email_compensation()
                        if draft.status == "sent" else None
                    )} for draft in drafts],
        "risks": [{"id": risk.id, "title": risk.title, "criticality": risk.criticality,
                   "status": risk.status, "confidence": risk.confidence,
                   "source_excerpt": risk.source_excerpt} for risk in risks],
        "completion_suggestions": [{"id": suggestion.id, "task_id": task.id, "task_title": task.title,
                                    "task_status": task.status, "confidence": suggestion.confidence,
                                    "evidence": suggestion.evidence, "status": suggestion.status}
                                   for suggestion, task in completion_rows],
        "evidence_refs": evidence_refs,
    }


def _message_workflow_state(row: Message, *, tasks: list[Task], drafts: list[ResponseDraft],
                            risks: list[Risk], completion_suggestions: list[TaskCompletionSuggestion]) -> tuple[str, str]:
    """Derive the operator-facing state without guessing external outcomes."""
    if not row.context_confirmed:
        return "needs_context_confirmation", "Связь с проектом или договором требует подтверждения"
    if row.status == "completed":
        return "completed", "Пользователь завершил обработку сообщения"
    if any(suggestion.status == "proposed" for suggestion in completion_suggestions):
        return "requires_action", "Нужно подтвердить или отклонить найденный результат задачи"
    if row.source_type == "email_outgoing" or any(draft.status == "sent" for draft in drafts):
        return "awaiting_reply", "Исходящее письмо отправлено или зафиксировано; ожидается ответ"
    if (row.analysis_required or row.source_type in {"email", "telegram"}
            or tasks or risks or any(draft.status in {"draft", "approved"} for draft in drafts)):
        return "requires_action", "Нужно проверить предложения, ответ или исходное сообщение"
    return "ready", "Автоматических предложений нет"


def _analyze_confirmed_message(db: Session, row: Message) -> tuple[list[Task], list[ResponseDraft], list[Risk], list[TaskCompletionSuggestion]]:
    """Materialize proposals once, and only after exact context confirmation.

    The called engines are already idempotent by message/source digest. Keeping
    ``analysis_required`` true until the final commit makes a crash retryable
    without creating duplicate proposals.
    """
    if not row.context_confirmed or not row.analysis_required:
        return [], [], [], []
    synthetic = StorageObject(
        id=f"message:{row.id}", name=row.source_name, mime_type="text/plain",
        parent_id="ai-secretary", content_text=row.content,
    )
    if row.source_type == "email_outgoing":
        tasks, drafts, risks = [], [], []
        completion_suggestions = _create_completion_suggestions(db, row)
    else:
        tasks = create_tasks_from_files(
            db, row.project_id, None, [synthetic], source_type=row.source_type,
        )
        drafts = create_response_drafts(
            db, row.project_id, None, [synthetic], ensure_response=row.source_type == "email",
        )
        risks, _decisions = create_governance_items(
            db, row.project_id, [synthetic], source_type=row.source_type,
        )
        completion_suggestions = []
    for task in tasks:
        task.message_id = row.id
        task.external_action_status = "proposed"
    for draft in drafts:
        draft.message_id = row.id
    row.analysis_required = False
    row.summary = (
        f"Исходящее письмо проверено. Возможных выполненных задач: {len(completion_suggestions)}. "
        "Требуется подтверждение пользователя."
        if row.source_type == "email_outgoing" else
        brief_summary(row.content, row.source_name, len(tasks), len(drafts), 0)
    )
    db.add(AuditLog(
        action="message_analysis_materialized", entity_type="message", entity_id=row.id,
        details=(f"tasks={len(tasks)}; drafts={len(drafts)}; risks={len(risks)}; "
                 f"completion_suggestions={len(completion_suggestions)}"),
    ))
    return tasks, drafts, risks, completion_suggestions


def _completion_candidate_score(task: Task, content: str) -> tuple[float, str]:
    words = lambda value: {word for word in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", (value or "").casefold()) if len(word) >= 4}
    task_words = words(f"{task.title} {task.description or ''}")
    message_words = words(content)
    overlap = sorted(task_words & message_words)
    lexical = len(overlap) / max(1, min(len(task_words), 8))
    completion_markers = ("выполнено", "готово", "направили", "направлено", "отправили", "завершено", "устранено", "согласовано")
    marker = next((value for value in completion_markers if value in content.casefold()), None)
    confidence = min(0.98, lexical + (0.35 if marker else 0))
    evidence = f"Совпали слова: {', '.join(overlap[:6]) or 'нет точных совпадений'}"
    if marker:
        evidence += f"; найден признак результата «{marker}»"
    return confidence, evidence


def _create_completion_suggestions(db: Session, row: Message) -> list[TaskCompletionSuggestion]:
    suggestions = []
    tasks = list(db.scalars(select(Task).where(
        Task.project_id == row.project_id,
        Task.status.not_in(("completed", "cancelled")),
    ).order_by(Task.due_date, Task.id)))
    for task in tasks:
        confidence, evidence = _completion_candidate_score(task, row.content)
        if confidence < 0.45:
            continue
        if db.scalar(select(TaskCompletionSuggestion.id).where(
            TaskCompletionSuggestion.message_id == row.id,
            TaskCompletionSuggestion.task_id == task.id,
        )):
            continue
        suggestion = TaskCompletionSuggestion(project_id=row.project_id, message_id=row.id, task_id=task.id,
                                              confidence=confidence, evidence=evidence, status="proposed")
        db.add(suggestion)
        suggestions.append(suggestion)
    return suggestions


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
    return {"messages": [_message_payload(db, row, actor=user) for row in rows], "count": len(rows)}


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
    return _message_payload(db, row, actor=user)


def ingest_message(payload: IncomingMessage, db: Session, user: User, *, mailbox_origin=None) -> dict:
    require_project_role(db, user, payload.project_id, "editor")
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    external_id = payload.source_external_id or f"manual:{uuid4()}"
    existing = db.scalar(select(Message).where(
        Message.mail_connection_id == mailbox_origin.mail_connection_id,
        Message.provider_message_id == external_id,
    )) if mailbox_origin else db.scalar(select(Message).where(
        Message.mail_connection_id.is_(None), Message.source_type == payload.source_type,
        Message.source_external_id == external_id))
    if existing:
        require_project_role(db, user, existing.project_id, "editor")
        if existing.organization_id != project.organization_id:
            raise HTTPException(409, "Message identity requires mailbox-scoped reconciliation")
        return _message_payload(db, existing, actor=user)
    if payload.routing_contract_id is not None:
        contract = db.get(Contract, payload.routing_contract_id)
        if contract is None or contract.project_id != payload.project_id:
            raise HTTPException(422, "Контакт связан с договором другого проекта")
        confidence, evidence = 0.99, payload.routing_evidence or "Проект и договор определены по email клиента"
    else:
        contract, confidence, evidence = _contract_candidate(db, payload.project_id, payload.content)
        if payload.routing_evidence:
            confidence, evidence = max(confidence, 0.99), payload.routing_evidence
    if payload.routing_confidence is not None:
        confidence = payload.routing_confidence
        evidence = payload.routing_evidence or "Требуется подтверждение проекта"
        if confidence < 0.90:
            contract = None
    row = Message(
        organization_id=project.organization_id, project_id=project.id, contract_id=contract.id if contract else None,
        created_by_user_id=user.id, source_type=payload.source_type, source_external_id=external_id,
        source_name=payload.source_name.strip(), source_url=payload.source_url,
        source_sender=payload.source_sender, source_thread_id=payload.source_thread_id,
        content=payload.content.strip(), attachments_json=json.dumps(payload.attachments, ensure_ascii=False),
        summary="Анализируется", context_confidence=confidence,
        context_evidence=evidence, context_confirmed=confidence >= 0.90,
        status=("filtered" if payload.automation_suppressed else
                "ready" if confidence >= 0.90 else "needs_context_confirmation"),
        analysis_required=not payload.automation_suppressed,
        mail_connection_id=mailbox_origin.mail_connection_id if mailbox_origin else None,
        provider_message_id=external_id if mailbox_origin else None,
        source_reference_id=mailbox_origin.source_reference_id if mailbox_origin else None,
    )
    db.add(row); db.flush()
    if mailbox_origin:
        from app.mailbox_identity.service import MailboxIdentityService
        MailboxIdentityService().record_provider_observed_origin(
            db, message=row, runtime=mailbox_origin.runtime,
            source=mailbox_origin.source, source_version=mailbox_origin.source_version,
            actor=user)
    if payload.automation_suppressed:
        tasks, drafts, risks, completion_suggestions = [], [], [], []
        row.analysis_required = False
        row.summary = (
            f"Автоматические действия не создавались: "
            f"{payload.automation_suppression_reason or 'массовое или рекламное письмо'}."
        )
    elif row.context_confirmed:
        tasks, drafts, risks, completion_suggestions = _analyze_confirmed_message(db, row)
    else:
        tasks, drafts, risks, completion_suggestions = [], [], [], []
        row.summary = (
            "Анализ отложен: сначала подтвердите проект и договор. "
            "Задачи, календарные действия и проекты ответов ещё не создавались."
        )
    db.add(AuditLog(action="message_processed", entity_type="message", entity_id=row.id,
                    details=f"source={row.source_type}; tasks={len(tasks)}; drafts={len(drafts)}; risks={len(risks)}; context={confidence:.0%}"))
    db.commit(); db.refresh(row)
    return _message_payload(db, row, actor=user)


@router.post("/inbox/{message_id}/completion-suggestions/{suggestion_id}")
def review_completion_suggestion(message_id: int, suggestion_id: int, payload: CompletionReview,
                                 db: Session = Depends(get_db), user: User = Depends(require_user)):
    suggestion = db.get(TaskCompletionSuggestion, suggestion_id)
    if suggestion is None or suggestion.message_id != message_id:
        raise HTTPException(404, "Предложение о выполнении задачи не найдено")
    require_project_role(db, user, suggestion.project_id, "editor")
    task = db.get(Task, suggestion.task_id)
    message = db.get(Message, message_id)
    if (task is None or message is None or task.project_id != suggestion.project_id
            or message.project_id != suggestion.project_id or not message.context_confirmed):
        raise HTTPException(409, "Контекст письма или задачи изменился; требуется повторная проверка")
    require_project_role(db, user, message.project_id, "editor")
    require_project_role(db, user, task.project_id, "editor")
    if suggestion.status != "proposed":
        return {"id": suggestion.id, "status": suggestion.status, "already_reviewed": True}
    suggestion.status = payload.status
    suggestion.reviewed_by_user_id = user.id
    suggestion.reviewed_at = datetime.now(timezone.utc)
    if payload.status == "confirmed" and task is not None and task.status != "completed":
        old_status = task.status
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        task.result_note = f"Выполнение подтверждено по исходящему письму: {message.source_name if message else message_id}"
        db.add(TaskHistory(task_id=task.id, action="completed_from_outgoing_email", old_status=old_status,
                           new_status="completed", result_note=task.result_note, changed_by_user_id=user.id,
                           details=f"message={message_id}; suggestion={suggestion.id}"))
    db.add(AuditLog(action="outgoing_completion_reviewed", entity_type="task_completion_suggestion", entity_id=suggestion.id,
                    details=f"message={message_id}; task={suggestion.task_id}; status={payload.status}"))
    db.commit()
    return {"id": suggestion.id, "status": suggestion.status, "task_id": suggestion.task_id,
            "task_status": task.status if task else None, "already_reviewed": False}


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
    _analyze_confirmed_message(db, row)
    db.commit(); db.refresh(row)
    return _message_payload(db, row, actor=user)


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
    for row in rows:
        _analyze_confirmed_message(db, row)
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
