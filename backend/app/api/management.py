from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.governance_engine import create_governance_items
from app.models.audit_log import AuditLog
from app.models.governance import Decision, Risk
from app.models.job import BackgroundJob
from app.models.management import ManagementHistory, Meeting, Notification, NotificationPolicy, Obligation
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.user import User
from app.organizer_engine.types import DriveFile
from app.task_engine import create_tasks_from_files
from app.notification_escalation import ALLOWED_CHANNELS, deadline_utc, outside_quiet_hours, require_iana_timezone

router = APIRouter(prefix="/management", tags=["management"])


class ObligationUpdate(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
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
    expected_record_version: int = Field(default=1, ge=1)
    minutes: str = Field(min_length=3, max_length=50000)
    status: str = Field(default="completed", pattern="^(held|completed|cancelled)$")


class NotificationPolicyUpdate(BaseModel):
    expected_record_version: int = Field(default=0, ge=0)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=100)
    deadline_local_time: time = time(9, 0)
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)
    escalation_delays: list[int] = Field(default_factory=lambda: [0, 60, 240], min_length=1, max_length=10)
    channels: list[str] = Field(default_factory=lambda: ["in_app"], min_length=1, max_length=3)
    enabled: bool = True
    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        require_iana_timezone(value)
        return value

    @field_validator("escalation_delays")
    @classmethod
    def valid_delays(cls, value: list[int]) -> list[int]:
        if any(type(delay) is not int or delay < 0 or delay > 60 * 24 * 30 for delay in value):
            raise ValueError("escalation delays must be non-negative minutes within 30 days")
        if value != sorted(set(value)):
            raise ValueError("escalation delays must be unique and sorted")
        return value

    @field_validator("channels")
    @classmethod
    def valid_channels(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(channel not in ALLOWED_CHANNELS for channel in value):
            raise ValueError("unsupported or duplicate notification channel")
        return value


class NotificationRead(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)


def _obligation_payload(item: Obligation) -> dict:
    return {"id": item.id, "record_version": item.record_version, "project_id": item.project_id, "contract_id": item.contract_id,
            "task_id": item.task_id, "title": item.title, "status": item.status,
            "due_date": item.due_date, "result_note": item.result_note,
            "source_type": item.source_type, "source_id": item.source_id, "source_name": item.source_name,
            "source_excerpt": item.source_excerpt, "source_hash": item.source_hash, "confidence": item.confidence}


def _project_organization_id(db: Session, project_id: int) -> int:
    organization_id = db.scalar(select(Project.organization_id).where(Project.id == project_id))
    if organization_id is None:
        raise HTTPException(404, "Project not found")
    return organization_id


def append_management_history(db: Session, *, project_id: int, entity_type: str, entity_id: int,
                              record_version: int, action: str, actor_user_id: int,
                              old_values: dict, new_values: dict, evidence=None, reason: str | None = None):
    db.add(ManagementHistory(
        organization_id=_project_organization_id(db, project_id), project_id=project_id,
        entity_type=entity_type, entity_id=entity_id, record_version=record_version,
        action=action, actor_user_id=actor_user_id,
        old_values=jsonable_encoder(old_values), new_values=jsonable_encoder(new_values),
        evidence=jsonable_encoder(evidence) if evidence is not None else None, reason=reason,
    ))


def _locked_versioned(db: Session, model, entity_id: int, expected: int, label: str):
    item = db.scalar(select(model).where(model.id == entity_id).with_for_update())
    if item is None:
        raise HTTPException(404, f"{label} not found")
    if item.record_version != expected:
        raise HTTPException(409, {"code": "record_version_conflict", "expected": expected,
                                  "actual": item.record_version})
    return item


class _DeferredCommitSession:
    """Let legacy extractors flush, while the endpoint owns the atomic commit."""
    def __init__(self, session: Session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def commit(self):
        self._session.flush()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _policy_payload(policy: NotificationPolicy) -> dict:
    return {"id": policy.id, "record_version": policy.record_version,
            "organization_id": policy.organization_id, "project_id": policy.project_id,
            "user_id": policy.user_id, "timezone": policy.timezone,
            "deadline_local_time": policy.deadline_local_time,
            "quiet_start": policy.quiet_start, "quiet_end": policy.quiet_end,
            "escalation_delays": list(policy.escalation_delays or []),
            "channels": list(policy.channels or []), "enabled": policy.enabled}


def _policy_for_refresh(db: Session, project_id: int, user: User) -> NotificationPolicy:
    organization_id = _project_organization_id(db, project_id)
    policy = db.scalar(select(NotificationPolicy).where(
        NotificationPolicy.project_id == project_id, NotificationPolicy.user_id == user.id,
    ).with_for_update())
    if policy is not None:
        if policy.organization_id != organization_id:
            raise HTTPException(409, "Notification policy tenant binding is invalid")
        return policy
    candidate = NotificationPolicy(organization_id=organization_id,
                                   project_id=project_id, user_id=user.id)
    try:
        with db.begin_nested():
            db.add(candidate); db.flush()
        return candidate
    except IntegrityError:
        policy = db.scalar(select(NotificationPolicy).where(
            NotificationPolicy.project_id == project_id, NotificationPolicy.user_id == user.id,
        ).with_for_update())
        if policy is None:
            raise
        return policy


@router.get("/notification-policy")
def get_notification_policy(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    policy = _policy_for_refresh(db, project_id, user)
    db.commit(); db.refresh(policy)
    return _policy_payload(policy)


@router.put("/notification-policy")
def update_notification_policy(project_id: int, payload: NotificationPolicyUpdate,
                               db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    organization_id = _project_organization_id(db, project_id)
    policy = db.scalar(select(NotificationPolicy).where(
        NotificationPolicy.project_id == project_id, NotificationPolicy.user_id == user.id,
    ).with_for_update())
    if policy is None:
        if payload.expected_record_version != 0:
            raise HTTPException(409, {"code": "record_version_conflict", "expected": payload.expected_record_version,
                                      "actual": 0})
        policy = NotificationPolicy(organization_id=organization_id, project_id=project_id, user_id=user.id,
                                    **payload.model_dump(exclude={"expected_record_version"}))
        db.add(policy); db.flush()
        old = {}
    else:
        if policy.organization_id != organization_id:
            raise HTTPException(409, "Notification policy tenant binding is invalid")
        if policy.record_version != payload.expected_record_version:
            raise HTTPException(409, {"code": "record_version_conflict", "expected": payload.expected_record_version,
                                      "actual": policy.record_version})
        old = _policy_payload(policy)
        for key, value in payload.model_dump(exclude={"expected_record_version"}).items():
            setattr(policy, key, value)
        policy.record_version += 1
    append_management_history(db, project_id=project_id, entity_type="notification_policy", entity_id=policy.id,
                              record_version=policy.record_version, action="created" if not old else "updated",
                              actor_user_id=user.id, old_values=old, new_values=_policy_payload(policy),
                              evidence={"channels": list(policy.channels)}, reason="Политика уведомлений изменена")
    db.commit(); db.refresh(policy)
    return _policy_payload(policy)


@router.get("/obligations")
def obligations(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
                status: str | None = None, cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be between 1 and 200")
    query = select(Obligation).where(Obligation.project_id == project_id)
    if status: query = query.where(Obligation.status == status)
    if cursor is not None: query = query.where(Obligation.id < cursor)
    rows = list(db.scalars(query.order_by(Obligation.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit; rows = rows[:limit]
    return {"obligations": [_obligation_payload(row) for row in rows], "count": len(rows),
            "next_cursor": rows[-1].id if has_more and rows else None}


@router.patch("/obligations/{obligation_id}")
def update_obligation(obligation_id: int, payload: ObligationUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = _locked_versioned(db, Obligation, obligation_id, payload.expected_record_version, "Obligation")
    require_project_role(db, user, item.project_id, "editor")
    if payload.status in {"fulfilled", "breached"} and not (payload.result_note or item.result_note or "").strip():
        raise HTTPException(422, "Укажите подтверждаемый результат или основание нарушения")
    old = _obligation_payload(item)
    item.status = payload.status
    if payload.result_note is not None:
        item.result_note = payload.result_note.strip() or None
    item.record_version += 1
    append_management_history(db, project_id=item.project_id, entity_type="obligation", entity_id=item.id,
                              record_version=item.record_version, action="updated", actor_user_id=user.id,
                              old_values=old, new_values=_obligation_payload(item),
                              evidence={"source_id": item.source_id, "source_hash": item.source_hash},
                              reason=item.result_note)
    db.add(AuditLog(action="obligation_updated", entity_type="obligation", entity_id=item.id,
                    details=f"status={item.status}; user={user.id}"))
    db.commit(); db.refresh(item)
    return _obligation_payload(item)


@router.get("/meetings")
def meetings(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
             status: str | None = None, cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200: raise HTTPException(422, "limit must be between 1 and 200")
    query = select(Meeting).where(Meeting.project_id == project_id)
    if status: query = query.where(Meeting.status == status)
    if cursor is not None: query = query.where(Meeting.id < cursor)
    rows = list(db.scalars(query.order_by(Meeting.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit; rows = rows[:limit]
    return {"meetings": [{"id": x.id, "record_version": x.record_version, "project_id": x.project_id, "contract_id": x.contract_id,
                           "title": x.title, "scheduled_at": x.scheduled_at, "participants": x.participants,
                           "agenda": x.agenda, "minutes": x.minutes, "status": x.status} for x in rows], "count": len(rows),
            "next_cursor": rows[-1].id if has_more and rows else None}


@router.post("/meetings")
def create_meeting(payload: MeetingCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    if payload.contract_id is not None and not db.scalar(select(Contract.id).where(Contract.id == payload.contract_id, Contract.project_id == payload.project_id)):
        raise HTTPException(422, "Договор не принадлежит выбранному проекту")
    item = Meeting(**payload.model_dump(), created_by_user_id=user.id)
    db.add(item); db.flush()
    append_management_history(db, project_id=item.project_id, entity_type="meeting", entity_id=item.id,
                              record_version=item.record_version, action="created", actor_user_id=user.id,
                              old_values={}, new_values={"title": item.title, "status": item.status},
                              reason=item.agenda)
    db.add(AuditLog(action="meeting_created", entity_type="meeting", entity_id=item.id, details=f"user={user.id}"))
    db.commit(); db.refresh(item)
    return {"id": item.id, "status": item.status, "record_version": item.record_version}


@router.patch("/meetings/{meeting_id}")
def finish_meeting(meeting_id: int, payload: MeetingUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = _locked_versioned(db, Meeting, meeting_id, payload.expected_record_version, "Meeting")
    require_project_role(db, user, item.project_id, "editor")
    old = {"minutes": item.minutes, "status": item.status}
    item.minutes, item.status = payload.minutes.strip(), payload.status
    item.record_version += 1
    db.flush()
    tasks = []; risks = []; decisions = []
    if payload.status == "completed":
        source = DriveFile(id=f"meeting:{item.id}", name=f"Протокол: {item.title}", mime_type="text/plain",
                           parent_id="meetings", content_text=item.minutes)
        deferred = _DeferredCommitSession(db)
        tasks = create_tasks_from_files(deferred, item.project_id, None, [source], source_type="meeting")
        risks, decisions = create_governance_items(deferred, item.project_id, [source], source_type="meeting")
    append_management_history(db, project_id=item.project_id, entity_type="meeting", entity_id=item.id,
                              record_version=item.record_version, action="minutes_recorded", actor_user_id=user.id,
                              old_values=old, new_values={"minutes": item.minutes, "status": item.status},
                              evidence={"tasks": [x.id for x in tasks], "risks": [x.id for x in risks],
                                        "decisions": [x.id for x in decisions]}, reason=item.minutes)
    db.add(AuditLog(action="meeting_minutes_recorded", entity_type="meeting", entity_id=item.id,
                    details=f"status={item.status}; tasks={len(tasks)}; risks={len(risks)}; decisions={len(decisions)}"))
    db.commit()
    return {"id": item.id, "status": item.status, "record_version": item.record_version,
            "tasks": len(tasks), "risks": len(risks), "decisions": len(decisions)}


def _ensure_notification(db: Session, user_id: int, project_id: int, kind: str, title: str, body: str,
                         entity_type: str, entity_id: int, key: str):
    existing = db.scalar(select(Notification).where(Notification.user_id == user_id,
                                                     Notification.dedupe_key == key))
    if existing is not None:
        return existing
    candidate = Notification(project_id=project_id, user_id=user_id, kind=kind, title=title, body=body,
                             entity_type=entity_type, entity_id=entity_id, dedupe_key=key)
    try:
        with db.begin_nested():
            db.add(candidate); db.flush()
        return candidate
    except IntegrityError:
        existing = db.scalar(select(Notification).where(Notification.user_id == user_id,
                                                         Notification.dedupe_key == key))
        if existing is None:
            raise
        return existing


def _ensure_escalation_job(db: Session, *, policy: NotificationPolicy, notification: Notification,
                           obligation: Obligation, step: int, delay: int, available_at: datetime) -> BackgroundJob:
    key = f"notification-escalation:{policy.user_id}:{obligation.id}:{obligation.due_date}:{step}"
    existing = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == key))
    if existing is not None:
        return existing
    payload = {"organization_id": policy.organization_id, "project_id": policy.project_id,
               "user_id": policy.user_id, "notification_id": notification.id,
               "obligation_id": obligation.id, "step": step, "delay_minutes": delay,
               "channels": list(policy.channels), "policy_record_version": policy.record_version}
    candidate = BackgroundJob(kind="notifications.escalation.proposal", payload=payload,
                              idempotency_key=key, available_at=available_at, max_attempts=5, priority=80)
    try:
        with db.begin_nested():
            db.add(candidate); db.flush()
        return candidate
    except IntegrityError:
        existing = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == key))
        if existing is None:
            raise
        return existing


@router.post("/notifications/refresh")
def refresh_notifications(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    policy = _policy_for_refresh(db, project_id, user)
    now = _utcnow()
    local_today = now.astimezone(require_iana_timezone(policy.timezone)).date()
    today, soon = local_today, local_today + timedelta(days=7)
    # Refresh is additive/idempotent. Existing read state is historical user data.
    for obligation in db.scalars(select(Obligation).where(
        Obligation.project_id == project_id,
        Obligation.status.in_(["confirmed", "in_progress"]),
    )).all():
        if obligation.due_date and obligation.due_date <= soon:
            kind = "overdue" if obligation.due_date < today else "deadline"
            notification = _ensure_notification(
                db, user.id, project_id, kind, obligation.title[:240],
                f"Срок: {obligation.due_date.isoformat()}. Источник: {obligation.source_name}",
                "obligation", obligation.id, f"obligation:{obligation.id}:{kind}:{obligation.due_date}",
            )
            if policy.enabled:
                due = deadline_utc(obligation.due_date, policy.deadline_local_time, policy.timezone)
                for step, delay in enumerate(policy.escalation_delays):
                    available_at = outside_quiet_hours(
                        max(now, due + timedelta(minutes=delay)), timezone_name=policy.timezone,
                        quiet_start=policy.quiet_start, quiet_end=policy.quiet_end,
                    )
                    _ensure_escalation_job(db, policy=policy, notification=notification,
                                           obligation=obligation, step=step, delay=delay,
                                           available_at=available_at)
    for risk in db.scalars(select(Risk).where(Risk.project_id == project_id, Risk.status.in_(["confirmed", "mitigating"]))).all():
        _ensure_notification(db, user.id, project_id, "risk", risk.title, risk.source_excerpt,
                             "risk", risk.id, f"risk:{risk.id}:{risk.status}")
    for decision in db.scalars(select(Decision).where(Decision.project_id == project_id, Decision.status == "confirmed")).all():
        _ensure_notification(db, user.id, project_id, "decision", decision.question, decision.source_excerpt,
                             "decision", decision.id, f"decision:{decision.id}:{decision.status}")
    db.commit()
    return list_notifications(project_id, db=db, user=user)


@router.get("/notifications")
def list_notifications(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
                       kind: str | None = None, unread: bool | None = None,
                       cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200: raise HTTPException(422, "limit must be between 1 and 200")
    query = select(Notification).where(Notification.project_id == project_id, Notification.user_id == user.id)
    if kind: query = query.where(Notification.kind == kind)
    if unread is not None: query = query.where(Notification.is_read.is_(not unread))
    if cursor is not None: query = query.where(Notification.id < cursor)
    rows = list(db.scalars(query.order_by(Notification.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit; rows = rows[:limit]
    return {"notifications": [{"id": x.id, "record_version": x.record_version,
                                "kind": x.kind, "title": x.title, "body": x.body,
                                "entity_type": x.entity_type, "entity_id": x.entity_id,
                                "is_read": x.is_read, "created_at": x.created_at} for x in rows],
            "unread": len([x for x in rows if not x.is_read]),
            "next_cursor": rows[-1].id if has_more and rows else None}


@router.get("/history/{entity_type}/{entity_id}")
def management_history(entity_type: str, entity_id: int, project_id: int,
                       db: Session = Depends(get_db), user: User = Depends(require_user),
                       cursor: int | None = None, limit: int = 100):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 200: raise HTTPException(422, "limit must be between 1 and 200")
    query = select(ManagementHistory).where(
        ManagementHistory.project_id == project_id,
        ManagementHistory.entity_type == entity_type,
        ManagementHistory.entity_id == entity_id,
    )
    if cursor is not None: query = query.where(ManagementHistory.id < cursor)
    rows = list(db.scalars(query.order_by(ManagementHistory.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit; rows = rows[:limit]
    return {"history": [{"id": row.id, "record_version": row.record_version, "action": row.action,
                          "actor_user_id": row.actor_user_id, "old_values": row.old_values,
                          "new_values": row.new_values, "evidence": row.evidence,
                          "reason": row.reason, "created_at": row.created_at} for row in rows],
            "next_cursor": rows[-1].id if has_more and rows else None}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, payload: NotificationRead,
                      db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.scalar(select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == user.id,
    ).with_for_update())
    if item is None:
        raise HTTPException(404, "Notification not found")
    require_project_role(db, user, item.project_id, "viewer")
    if item.record_version != payload.expected_record_version:
        raise HTTPException(409, {"code": "record_version_conflict",
                                  "expected": payload.expected_record_version,
                                  "actual": item.record_version})
    if not item.is_read:
        item.is_read = True
        item.record_version += 1
        append_management_history(
            db, project_id=item.project_id, entity_type="notification", entity_id=item.id,
            record_version=item.record_version, action="read", actor_user_id=user.id,
            old_values={"is_read": False}, new_values={"is_read": True},
            evidence={"kind": item.kind, "entity_type": item.entity_type,
                      "entity_id": item.entity_id},
            reason="Пользователь отметил уведомление прочитанным",
        )
        db.commit(); db.refresh(item)
    return {"id": item.id, "record_version": item.record_version, "is_read": True}
