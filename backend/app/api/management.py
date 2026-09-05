from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.governance_engine import create_governance_items
from app.models.audit_log import AuditLog
from app.models.governance import Decision, GovernanceHistory, Risk
from app.models.management import Meeting, Notification, Obligation, ObligationHistory
from app.models.organization_contract import Contract
from app.models.user import User
from app.organizer_engine.types import DriveFile
from app.task_engine import create_tasks_from_files
from app.mvp3.attention import attention_page
from app.mvp3.lifecycle import ManagementConflict, ManagementDenied, ManagementLifecycle, normalized_task_state
from app.mvp3.meeting_digest import (
    DigestPreference,
    MeetingActionCandidate,
    MeetingProposalService,
    enqueue_digest,
)

router = APIRouter(prefix="/management", tags=["management"])


class ObligationUpdate(BaseModel):
    status: str = Field(pattern="^(confirmed|in_progress|fulfilled|breached|dismissed)$")
    result_note: str | None = Field(default=None, max_length=5000)


class EvidenceObligationCreate(BaseModel):
    project_id: int
    owner_user_id: int
    title: str = Field(min_length=2, max_length=500)
    contract_id: int | None = None
    due_date: date | None = None
    due_time: time | None = None
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=100)
    evidence_pins: list[dict] = Field(min_length=1, max_length=20)
    deadline_policy: dict | None = None


class LifecycleTransition(BaseModel):
    expected_version: int = Field(ge=1)
    status: str = Field(min_length=2, max_length=50)
    reason: str | None = Field(default=None, max_length=2000)
    result_note: str | None = Field(default=None, max_length=5000)


class GovernanceCreate(BaseModel):
    project_id: int
    owner_user_id: int
    title: str = Field(min_length=2, max_length=5000)
    evidence_pins: list[dict] = Field(min_length=1, max_length=20)
    criticality: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    obligation_id: int | None = None
    task_id: int | None = None
    risk_id: int | None = None


class GovernanceTransition(BaseModel):
    project_id: int
    expected_version: int = Field(ge=1)
    status: str = Field(min_length=2, max_length=50)
    reason: str | None = Field(default=None, max_length=2000)
    action_note: str | None = Field(default=None, max_length=5000)
    decision_text: str | None = Field(default=None, max_length=5000)


class EvidenceProposalCreate(BaseModel):
    project_id: int = Field(gt=0)
    candidates: list[MeetingActionCandidate] = Field(min_length=1, max_length=100)


class EvidenceProposalConfirm(BaseModel):
    project_id: int = Field(gt=0)
    expected_version: int = Field(ge=1)
    create_internal_task: bool = False


class DigestEnqueueRequest(BaseModel):
    project_id: int = Field(gt=0)
    timezone: str = Field(min_length=1, max_length=100)
    quiet_start: time
    quiet_end: time
    channel: Literal["in_app", "disabled"] = "in_app"
    local_date: date


_lifecycle = ManagementLifecycle()
_meeting_proposals = MeetingProposalService(_lifecycle)


def _lifecycle_error(exc):
    if isinstance(exc, ManagementConflict):
        raise HTTPException(409, "version_conflict") from exc
    raise HTTPException(422, str(exc)) from exc


class MeetingCreate(BaseModel):
    project_id: int
    contract_id: int | None = None
    title: str = Field(min_length=2, max_length=500)
    scheduled_at: datetime | None = None
    participants: str | None = Field(default=None, max_length=5000)
    agenda: str | None = Field(default=None, max_length=10000)


class MeetingUpdate(BaseModel):
    minutes: str = Field(min_length=3, max_length=50000)
    status: str = Field(default="completed", pattern="^(held|completed|cancelled)$")


def _obligation_payload(item: Obligation) -> dict:
    return {"id": item.id, "project_id": item.project_id, "contract_id": item.contract_id,
            "task_id": item.task_id, "title": item.title, "status": item.status,
            "due_date": item.due_date, "result_note": item.result_note,
            "source_type": item.source_type, "source_name": item.source_name,
            "source_excerpt": item.source_excerpt, "confidence": item.confidence,
            "record_version": item.record_version, "due_time": item.due_time,
            "timezone": item.timezone, "evidence_pins": item.evidence_pins or [],
            "review_state": item.review_state, "escalation_level": item.escalation_level,
            "deadline_policy": item.deadline_policy or {}}


@router.post("/v2/meetings/{meeting_id}/proposals")
def propose_meeting_actions(meeting_id: int, payload: EvidenceProposalCreate,
                            db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    try:
        result = _meeting_proposals.propose(
            db, project_id=payload.project_id, meeting_id=meeting_id,
            actor_user_id=user.id, candidates=payload.candidates,
        )
        db.commit()
        return {"proposals": result, "external_actions_created": False}
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.post("/v2/messages/{message_id}/proposals")
def propose_message_actions(message_id: int, payload: EvidenceProposalCreate,
                            db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    try:
        result = _meeting_proposals.propose_message(
            db, project_id=payload.project_id, message_id=message_id,
            actor_user_id=user.id, candidates=payload.candidates,
        )
        db.commit()
        return {"proposals": result, "external_actions_created": False}
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.post("/v2/proposals/{entity_type}/{entity_id}/confirm")
def confirm_evidence_proposal(entity_type: str, entity_id: int, payload: EvidenceProposalConfirm,
                              db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
    try:
        result = _meeting_proposals.confirm(
            db, project_id=payload.project_id, actor_user_id=user.id,
            entity_type=entity_type, entity_id=entity_id,
            expected_version=payload.expected_version,
            create_internal_task=payload.create_internal_task,
        )
        db.commit()
        return {"proposal": result, "external_actions_created": False}
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.post("/v2/digests")
def enqueue_management_digest(payload: DigestEnqueueRequest, db: Session = Depends(get_db),
                              user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "viewer")
    try:
        preference = DigestPreference(
            timezone=payload.timezone, quiet_start=payload.quiet_start,
            quiet_end=payload.quiet_end, channel=payload.channel,
        )
        job = enqueue_digest(
            db, project_id=payload.project_id, user_id=user.id,
            actor_user_id=user.id, preference=preference, local_date=payload.local_date,
        )
        return {"job_id": job.id, "status": job.status, "external_actions_created": False}
    except (ManagementDenied, ManagementConflict, ValueError) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.post("/v2/obligations")
def create_evidence_obligation(payload: EvidenceObligationCreate, db: Session = Depends(get_db),
                               user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    try:
        scope = _lifecycle.scope(db, project_id=payload.project_id, actor_user_id=user.id)
        row = _lifecycle.create_obligation(
            db, scope=scope, title=payload.title, owner_user_id=payload.owner_user_id,
            evidence_pins=payload.evidence_pins, due_date=payload.due_date,
            due_time=payload.due_time, timezone_name=payload.timezone,
            contract_id=payload.contract_id, deadline_policy=payload.deadline_policy,
        )
        db.commit(); db.refresh(row)
        return _obligation_payload(row)
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.patch("/v2/obligations/{obligation_id}")
def transition_evidence_obligation(obligation_id: int, payload: LifecycleTransition,
                                   db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(Obligation, obligation_id)
    if row is None:
        raise HTTPException(404, "Obligation not found")
    require_project_role(db, user, row.project_id, "editor")
    try:
        scope = _lifecycle.scope(db, project_id=row.project_id, actor_user_id=user.id)
        row = _lifecycle.transition_obligation(db, scope=scope, obligation_id=obligation_id,
                                               expected_version=payload.expected_version, status=payload.status,
                                               reason=payload.reason, result_note=payload.result_note)
        db.commit(); db.refresh(row)
        return _obligation_payload(row)
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.get("/v2/obligations/{obligation_id}/history")
def evidence_obligation_history(obligation_id: int, db: Session = Depends(get_db),
                                user: User = Depends(require_user)):
    row = db.get(Obligation, obligation_id)
    if row is None:
        raise HTTPException(404, "Obligation not found")
    require_project_role(db, user, row.project_id, "viewer")
    events = db.scalars(select(ObligationHistory).where(ObligationHistory.obligation_id == row.id)
                        .order_by(ObligationHistory.sequence)).all()
    return {"history": [{"sequence": event.sequence, "event": event.event,
                          "from_status": event.from_status, "to_status": event.to_status,
                          "record_version": event.resulting_version, "reason": event.reason,
                          "evidence_pins": event.evidence_pins or [], "occurred_at": event.occurred_at}
                         for event in events]}


@router.post("/v2/obligations/{obligation_id}/internal-task")
def map_obligation_task(obligation_id: int, expected_version: int, db: Session = Depends(get_db),
                        user: User = Depends(require_user)):
    row = db.get(Obligation, obligation_id)
    if row is None:
        raise HTTPException(404, "Obligation not found")
    require_project_role(db, user, row.project_id, "editor")
    try:
        scope = _lifecycle.scope(db, project_id=row.project_id, actor_user_id=user.id)
        task = _lifecycle.ensure_internal_task(db, scope=scope, obligation_id=row.id,
                                               expected_version=expected_version)
        db.commit(); db.refresh(task)
        return {"id": task.id, "status": task.status, "normalized_state": normalized_task_state(task.status),
                "external_action_status": task.external_action_status}
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.post("/v2/risks")
def create_evidence_risk(payload: GovernanceCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    try:
        scope = _lifecycle.scope(db, project_id=payload.project_id, actor_user_id=user.id)
        row = _lifecycle.create_risk(db, scope=scope, title=payload.title, owner_user_id=payload.owner_user_id,
                                     evidence_pins=payload.evidence_pins, criticality=payload.criticality,
                                     obligation_id=payload.obligation_id, task_id=payload.task_id)
        db.commit(); db.refresh(row)
        return {"id": row.id, "status": row.status, "record_version": row.record_version,
                "review_state": row.review_state, "evidence_pins": row.evidence_pins}
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.post("/v2/decisions")
def create_evidence_decision(payload: GovernanceCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    try:
        scope = _lifecycle.scope(db, project_id=payload.project_id, actor_user_id=user.id)
        row = _lifecycle.create_decision(db, scope=scope, question=payload.title, owner_user_id=payload.owner_user_id,
                                         evidence_pins=payload.evidence_pins, obligation_id=payload.obligation_id,
                                         task_id=payload.task_id, risk_id=payload.risk_id)
        db.commit(); db.refresh(row)
        return {"id": row.id, "status": row.status, "record_version": row.record_version,
                "review_state": row.review_state, "evidence_pins": row.evidence_pins}
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.patch("/v2/{entity_type}/{entity_id}")
def transition_governance(entity_type: str, entity_id: int, payload: GovernanceTransition,
                          db: Session = Depends(get_db), user: User = Depends(require_user)):
    if entity_type not in {"risks", "decisions"}:
        raise HTTPException(404, "Unsupported entity")
    require_project_role(db, user, payload.project_id, "editor")
    try:
        scope = _lifecycle.scope(db, project_id=payload.project_id, actor_user_id=user.id)
        row = _lifecycle.transition_governance(db, scope=scope, entity_type=entity_type[:-1], entity_id=entity_id,
                                               expected_version=payload.expected_version, status=payload.status,
                                               reason=payload.reason, action_note=payload.action_note,
                                               decision_text=payload.decision_text)
        db.commit(); db.refresh(row)
        return {"id": row.id, "status": row.status, "record_version": row.record_version,
                "review_state": row.review_state}
    except (ManagementDenied, ManagementConflict) as exc:
        db.rollback(); _lifecycle_error(exc)


@router.get("/v2/{entity_type}/{entity_id}/history")
def evidence_governance_history(entity_type: str, entity_id: int, project_id: int,
                                db: Session = Depends(get_db), user: User = Depends(require_user)):
    if entity_type not in {"risks", "decisions"}:
        raise HTTPException(404, "Unsupported entity")
    require_project_role(db, user, project_id, "viewer")
    kind = entity_type[:-1]
    events = db.scalars(select(GovernanceHistory).where(
        GovernanceHistory.project_id == project_id, GovernanceHistory.entity_type == kind,
        GovernanceHistory.entity_id == entity_id).order_by(GovernanceHistory.sequence)).all()
    if not events:
        raise HTTPException(404, "Entity not found")
    return {"history": [{"sequence": event.sequence, "event": event.event,
                          "from_status": event.from_status, "to_status": event.to_status,
                          "record_version": event.resulting_version, "reason": event.reason,
                          "evidence_pins": event.evidence_pins or [], "occurred_at": event.occurred_at}
                         for event in events]}


@router.get("/v2/attention")
def evidence_attention(project_id: int, offset: int = 0, limit: int = 50, kinds: str | None = None,
                       db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    if offset < 0 or limit < 1 or limit > 100:
        raise HTTPException(422, "Invalid pagination")
    return attention_page(db, project_id=project_id, kinds=set(kinds.split(",")) if kinds else None,
                          offset=offset, limit=limit)


@router.get("/obligations")
def obligations(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.scalars(select(Obligation).where(Obligation.project_id == project_id).order_by(Obligation.due_date, Obligation.id.desc())).all()
    return {"obligations": [_obligation_payload(row) for row in rows], "count": len(rows)}


@router.patch("/obligations/{obligation_id}")
def update_obligation(obligation_id: int, payload: ObligationUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(Obligation, obligation_id)
    if item is None:
        raise HTTPException(404, "Obligation not found")
    require_project_role(db, user, item.project_id, "editor")
    if payload.status in {"fulfilled", "breached"} and not (payload.result_note or item.result_note or "").strip():
        raise HTTPException(422, "Укажите подтверждаемый результат или основание нарушения")
    item.status = payload.status
    if payload.result_note is not None:
        item.result_note = payload.result_note.strip() or None
    db.add(AuditLog(action="obligation_updated", entity_type="obligation", entity_id=item.id,
                    details=f"status={item.status}; user={user.id}"))
    db.commit(); db.refresh(item)
    return _obligation_payload(item)


@router.get("/meetings")
def meetings(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.scalars(select(Meeting).where(Meeting.project_id == project_id).order_by(Meeting.scheduled_at.desc(), Meeting.id.desc())).all()
    return {"meetings": [{"id": x.id, "project_id": x.project_id, "contract_id": x.contract_id,
                           "title": x.title, "scheduled_at": x.scheduled_at, "participants": x.participants,
                           "agenda": x.agenda, "minutes": x.minutes, "status": x.status} for x in rows], "count": len(rows)}


@router.post("/meetings")
def create_meeting(payload: MeetingCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    if payload.contract_id is not None and not db.scalar(select(Contract.id).where(Contract.id == payload.contract_id, Contract.project_id == payload.project_id)):
        raise HTTPException(422, "Договор не принадлежит выбранному проекту")
    item = Meeting(**payload.model_dump(), created_by_user_id=user.id)
    db.add(item); db.flush()
    db.add(AuditLog(action="meeting_created", entity_type="meeting", entity_id=item.id, details=f"user={user.id}"))
    db.commit(); db.refresh(item)
    return {"id": item.id, "status": item.status}


@router.patch("/meetings/{meeting_id}")
def finish_meeting(meeting_id: int, payload: MeetingUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(Meeting, meeting_id)
    if item is None:
        raise HTTPException(404, "Meeting not found")
    require_project_role(db, user, item.project_id, "editor")
    item.minutes, item.status = payload.minutes.strip(), payload.status
    db.commit(); db.refresh(item)
    tasks = []; risks = []; decisions = []
    if payload.status == "completed":
        source = DriveFile(id=f"meeting:{item.id}", name=f"Протокол: {item.title}", mime_type="text/plain",
                           parent_id="meetings", content_text=item.minutes)
        tasks = create_tasks_from_files(db, item.project_id, None, [source], source_type="meeting")
        risks, decisions = create_governance_items(db, item.project_id, [source], source_type="meeting")
    db.add(AuditLog(action="meeting_minutes_recorded", entity_type="meeting", entity_id=item.id,
                    details=f"status={item.status}; tasks={len(tasks)}; risks={len(risks)}; decisions={len(decisions)}"))
    db.commit()
    return {"id": item.id, "status": item.status, "tasks": len(tasks), "risks": len(risks), "decisions": len(decisions)}


def _ensure_notification(db: Session, user_id: int, project_id: int, kind: str, title: str, body: str,
                         entity_type: str, entity_id: int, key: str):
    if not db.scalar(select(Notification.id).where(Notification.user_id == user_id, Notification.dedupe_key == key)):
        db.add(Notification(project_id=project_id, user_id=user_id, kind=kind, title=title, body=body,
                            entity_type=entity_type, entity_id=entity_id, dedupe_key=key))


@router.post("/notifications/refresh")
def refresh_notifications(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    today, soon = date.today(), date.today() + timedelta(days=7)
    # The notification centre is a current control snapshot, not the audit log.
    # Rebuild it fully so stale or previously-read alerts do not survive a refresh.
    db.execute(delete(Notification).where(Notification.project_id == project_id, Notification.user_id == user.id))
    for obligation in db.scalars(select(Obligation).where(
        Obligation.project_id == project_id,
        Obligation.status.in_(["confirmed", "in_progress"]),
    )).all():
        if obligation.due_date and obligation.due_date <= soon:
            kind = "overdue" if obligation.due_date < today else "deadline"
            _ensure_notification(db, user.id, project_id, kind, obligation.title[:240],
                                 f"Срок: {obligation.due_date.isoformat()}. Источник: {obligation.source_name}",
                                 "obligation", obligation.id, f"obligation:{obligation.id}:{kind}:{obligation.due_date}")
    for risk in db.scalars(select(Risk).where(Risk.project_id == project_id, Risk.status.in_(["confirmed", "mitigating"]))).all():
        _ensure_notification(db, user.id, project_id, "risk", risk.title, risk.source_excerpt,
                             "risk", risk.id, f"risk:{risk.id}:{risk.status}")
    for decision in db.scalars(select(Decision).where(Decision.project_id == project_id, Decision.status == "confirmed")).all():
        _ensure_notification(db, user.id, project_id, "decision", decision.question, decision.source_excerpt,
                             "decision", decision.id, f"decision:{decision.id}:{decision.status}")
    db.commit()
    return list_notifications(project_id, db, user)


@router.get("/notifications")
def list_notifications(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    rows = db.scalars(select(Notification).where(Notification.project_id == project_id, Notification.user_id == user.id)
                      .order_by(Notification.is_read, Notification.created_at.desc())).all()
    return {"notifications": [{"id": x.id, "kind": x.kind, "title": x.title, "body": x.body,
                                "entity_type": x.entity_type, "entity_id": x.entity_id,
                                "is_read": x.is_read, "created_at": x.created_at} for x in rows],
            "unread": len([x for x in rows if not x.is_read])}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(Notification, notification_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    require_project_role(db, user, item.project_id, "viewer")
    item.is_read = True; db.commit()
    return {"id": item.id, "is_read": True}
