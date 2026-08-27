from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.governance_engine import create_governance_items
from app.models.audit_log import AuditLog
from app.models.governance import Decision, Risk
from app.models.management import Meeting, Notification, Obligation
from app.models.organization_contract import Contract
from app.models.task import Task
from app.models.user import User
from app.organizer_engine.types import DriveFile
from app.task_engine import create_tasks_from_files

router = APIRouter(prefix="/management", tags=["management"])


class ObligationUpdate(BaseModel):
    status: str = Field(pattern="^(confirmed|in_progress|fulfilled|breached|dismissed)$")
    result_note: str | None = Field(default=None, max_length=5000)


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
            "source_excerpt": item.source_excerpt, "confidence": item.confidence}


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
    for task in db.scalars(select(Task).where(Task.project_id == project_id, Task.status.in_(["assigned", "in_progress"]))).all():
        if task.due_date and task.due_date <= soon:
            kind = "overdue" if task.due_date < today else "deadline"
            _ensure_notification(db, user.id, project_id, kind, task.title,
                                 f"Срок: {task.due_date.isoformat()}. Источник: {task.source_file_name}", "task", task.id,
                                 f"task:{task.id}:{kind}:{task.due_date}")
    for risk in db.scalars(select(Risk).where(Risk.project_id == project_id, Risk.status.in_(["needs_confirmation", "confirmed", "mitigating"]))).all():
        _ensure_notification(db, user.id, project_id, "risk", risk.title, risk.source_excerpt,
                             "risk", risk.id, f"risk:{risk.id}:{risk.status}")
    for decision in db.scalars(select(Decision).where(Decision.project_id == project_id, Decision.status.in_(["needs_confirmation", "confirmed"]))).all():
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
