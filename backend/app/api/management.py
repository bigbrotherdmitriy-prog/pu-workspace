from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.governance import Decision, Risk
from app.models.job import BackgroundJob
from app.models.management import (
    BookableResource, ManagementHistory, Meeting, MeetingParticipant,
    MeetingProposal, MeetingResource, MeetingSourceBinding, Notification,
    NotificationPolicy, Obligation,
)
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_contact import ProjectContact
from app.models.project_member import ProjectMember
from app.models.user import User
from app.notification_escalation import ALLOWED_CHANNELS, deadline_utc, outside_quiet_hours, require_iana_timezone
from app.attention_read_model import build_attention_feed
from app.mvp3.meeting_proposals import (
    MeetingProposalConflict, MeetingProposalDenied, bind_current_source,
    confirm_proposal, serialize_proposal,
)
from app.models.v54_pilot import SourceVersion
from app.source_evidence.meeting_authority import MeetingSourceDenied, list_current_local_upload_sources

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
    duration_minutes: int | None = Field(default=None, ge=1, le=10080)
    participants: str | None = Field(default=None, max_length=5000)
    participant_user_ids: list[int] = Field(default_factory=list, max_length=200)
    participant_contact_ids: list[int] = Field(default_factory=list, max_length=200)
    resource_ids: list[int] = Field(default_factory=list, max_length=100)
    agenda: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def validate_schedule_and_participants(self):
        if len(self.participant_user_ids) != len(set(self.participant_user_ids)):
            raise ValueError("participant_user_ids must be unique")
        if len(self.participant_contact_ids) != len(set(self.participant_contact_ids)):
            raise ValueError("participant_contact_ids must be unique")
        if len(self.resource_ids) != len(set(self.resource_ids)):
            raise ValueError("resource_ids must be unique")
        if self.scheduled_at is None:
            if self.duration_minutes is not None:
                raise ValueError("duration_minutes requires scheduled_at")
        elif self.duration_minutes is None:
            self.duration_minutes = 60
        return self


class BookableResourceCreate(BaseModel):
    project_id: int
    kind: str = Field(pattern="^(room|equipment|other)$")
    name: str = Field(min_length=2, max_length=500)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=100)
    capacity: int | None = Field(default=None, ge=1, le=100000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("resource name must contain at least two non-space characters")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        require_iana_timezone(value)
        return value


class BookableResourceUpdate(BaseModel):
    expected_record_version: int = Field(ge=1)
    kind: str | None = Field(default=None, pattern="^(room|equipment|other)$")
    name: str | None = Field(default=None, min_length=2, max_length=500)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    capacity: int | None = Field(default=None, ge=1, le=100000)
    active: bool | None = None

    @model_validator(mode="after")
    def non_nullable_fields_cannot_be_cleared(self):
        for field in ("kind", "name", "timezone", "active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) < 2:
            raise ValueError("resource name must contain at least two non-space characters")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            require_iana_timezone(value)
        return value


class MeetingUpdate(BaseModel):
    expected_record_version: int = Field(default=1, ge=1)
    minutes: str = Field(min_length=3, max_length=50000)
    status: str = Field(default="completed", pattern="^(held|completed|cancelled)$")


class MeetingSourceBindingCreate(BaseModel):
    expected_record_version: int = Field(ge=1)
    command_id: str
    source_id: str
    source_version_id: str
    evidence_id: str
    materialization_id: str


class MeetingProposalConfirm(BaseModel):
    expected_record_version: int = Field(ge=1)
    command_id: str


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
                              old_values: dict, new_values: dict, evidence=None, reason: str | None = None,
                              idempotency_key: str | None = None, command_hash: str | None = None):
    db.add(ManagementHistory(
        organization_id=_project_organization_id(db, project_id), project_id=project_id,
        entity_type=entity_type, entity_id=entity_id, record_version=record_version,
        action=action, actor_user_id=actor_user_id,
        idempotency_key=idempotency_key, command_hash=command_hash,
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


def _bookable_resource_payload(item: BookableResource) -> dict:
    return {
        "id": item.id,
        "record_version": item.record_version,
        "organization_id": item.organization_id,
        "managing_project_id": item.managing_project_id,
        "kind": item.kind,
        "name": item.name,
        "timezone": item.timezone,
        "capacity": item.capacity,
        "active": item.active,
    }


@router.get("/resources")
def bookable_resources(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user),
                       include_inactive: bool = False, cursor: int | None = None, limit: int = 200):
    require_project_role(db, user, project_id, "viewer")
    if not 1 <= limit <= 500:
        raise HTTPException(422, "limit must be between 1 and 500")
    organization_id = _project_organization_id(db, project_id)
    query = select(BookableResource).where(BookableResource.organization_id == organization_id)
    if not include_inactive:
        query = query.where(BookableResource.active.is_(True))
    if cursor is not None:
        query = query.where(BookableResource.id < cursor)
    rows = list(db.scalars(query.order_by(BookableResource.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "resources": [_bookable_resource_payload(row) for row in rows],
        "count": len(rows),
        "next_cursor": rows[-1].id if has_more and rows else None,
    }


@router.post("/resources")
def create_bookable_resource(payload: BookableResourceCreate, db: Session = Depends(get_db),
                             user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
    organization_id = _project_organization_id(db, payload.project_id)
    item = BookableResource(
        organization_id=organization_id,
        managing_project_id=payload.project_id,
        created_by_user_id=user.id,
        kind=payload.kind,
        name=payload.name.strip(),
        timezone=payload.timezone,
        capacity=payload.capacity,
        active=True,
    )
    db.add(item); db.flush()
    append_management_history(
        db, project_id=payload.project_id, entity_type="bookable_resource", entity_id=item.id,
        record_version=item.record_version, action="created", actor_user_id=user.id,
        old_values={}, new_values=_bookable_resource_payload(item),
    )
    db.add(AuditLog(action="bookable_resource_created", entity_type="bookable_resource",
                    entity_id=item.id, details=f"project={payload.project_id}; user={user.id}"))
    db.commit(); db.refresh(item)
    return _bookable_resource_payload(item)


@router.patch("/resources/{resource_id}")
def update_bookable_resource(resource_id: int, payload: BookableResourceUpdate,
                             db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = _locked_versioned(db, BookableResource, resource_id, payload.expected_record_version,
                             "Bookable resource")
    require_project_role(db, user, item.managing_project_id, "manager")
    project = db.get(Project, item.managing_project_id)
    if project is None or project.organization_id != item.organization_id:
        raise HTTPException(409, "Bookable resource tenant binding is invalid")
    old = _bookable_resource_payload(item)
    changes = payload.model_dump(exclude={"expected_record_version"}, exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    for field, value in changes.items():
        setattr(item, field, value)
    item.record_version += 1
    db.flush()
    append_management_history(
        db, project_id=item.managing_project_id, entity_type="bookable_resource", entity_id=item.id,
        record_version=item.record_version, action="updated", actor_user_id=user.id,
        old_values=old, new_values=_bookable_resource_payload(item),
    )
    db.add(AuditLog(action="bookable_resource_updated", entity_type="bookable_resource",
                    entity_id=item.id, details=f"active={item.active}; user={user.id}"))
    db.commit(); db.refresh(item)
    return _bookable_resource_payload(item)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _meeting_participant_payloads(db: Session, meeting_id: int) -> list[dict]:
    rows = db.execute(
        select(MeetingParticipant, User, ProjectContact)
        .outerjoin(User, User.id == MeetingParticipant.user_id)
        .outerjoin(ProjectContact, ProjectContact.id == MeetingParticipant.contact_id)
        .where(MeetingParticipant.meeting_id == meeting_id)
        .order_by(MeetingParticipant.id)
    ).all()
    return [
        {
            "kind": "user" if participant.user_id is not None else "contact",
            "id": participant.user_id if participant.user_id is not None else participant.contact_id,
            "name": user.name if user is not None else contact.name,
            "email": user.email if user is not None else contact.email,
        }
        for participant, user, contact in rows
    ]


def _meeting_resource_payloads(db: Session, meeting_id: int) -> list[dict]:
    rows = db.execute(
        select(MeetingResource, BookableResource)
        .join(BookableResource, BookableResource.id == MeetingResource.resource_id)
        .where(MeetingResource.meeting_id == meeting_id)
        .order_by(MeetingResource.id)
    ).all()
    return [
        {
            "id": resource.id,
            "kind": resource.kind,
            "name": resource.name,
            "timezone": resource.timezone,
            "capacity": resource.capacity,
            "active": resource.active,
        }
        for _reservation, resource in rows
    ]


def _meeting_conflicts(db: Session, meeting: Meeting, actor: User) -> list[dict]:
    if meeting.scheduled_at is None or meeting.duration_minutes is None:
        return []
    participants = _meeting_participant_payloads(db, meeting.id)
    resources = _meeting_resource_payloads(db, meeting.id)
    user_ids = [row["id"] for row in participants if row["kind"] == "user"]
    contact_ids = [row["id"] for row in participants if row["kind"] == "contact"]
    resource_ids = [row["id"] for row in resources]
    if not user_ids and not contact_ids and not resource_ids:
        return []
    candidate_queries = []
    identity_filters = []
    if user_ids:
        identity_filters.append(MeetingParticipant.user_id.in_(user_ids))
    if contact_ids:
        identity_filters.append(MeetingParticipant.contact_id.in_(contact_ids))
    if identity_filters:
        candidate_queries.append(select(MeetingParticipant.meeting_id).where(or_(*identity_filters)))
    if resource_ids:
        candidate_queries.append(select(MeetingResource.meeting_id).where(
            MeetingResource.resource_id.in_(resource_ids),
        ))
    meeting_project = db.get(Project, meeting.project_id)
    candidate_filter = or_(*(Meeting.id.in_(query) for query in candidate_queries))
    candidates = list(db.scalars(
        select(Meeting)
        .join(Project, Project.id == Meeting.project_id)
        .where(
            Meeting.id != meeting.id,
            candidate_filter,
            Meeting.scheduled_at.is_not(None),
            Meeting.duration_minutes.is_not(None),
            Meeting.status != "cancelled",
            Project.organization_id == meeting_project.organization_id,
        )
        .order_by(Meeting.scheduled_at, Meeting.id)
    ))
    current_start = _aware(meeting.scheduled_at)
    current_end = current_start + timedelta(minutes=meeting.duration_minutes)
    identity_map = {(row["kind"], row["id"]): row for row in participants}
    resource_map = {row["id"]: row for row in resources}
    conflicts = []
    for candidate in candidates:
        candidate_start = _aware(candidate.scheduled_at)
        candidate_end = candidate_start + timedelta(minutes=candidate.duration_minutes)
        if not (current_start < candidate_end and candidate_start < current_end):
            continue
        candidate_participants = _meeting_participant_payloads(db, candidate.id)
        shared = [identity_map[(row["kind"], row["id"])] for row in candidate_participants
                  if (row["kind"], row["id"]) in identity_map]
        candidate_resources = _meeting_resource_payloads(db, candidate.id)
        shared_resources = [resource_map[row["id"]] for row in candidate_resources if row["id"] in resource_map]
        if not shared and not shared_resources:
            continue
        visible = actor.is_admin or db.scalar(select(ProjectMember.id).where(
            ProjectMember.project_id == candidate.project_id,
            ProjectMember.user_id == actor.id,
        )) is not None
        conflicts.append({
            "meeting_id": candidate.id if visible else None,
            "project_id": candidate.project_id if visible else None,
            "title": candidate.title if visible else "Занято в другом проекте",
            "overlap_from": max(current_start, candidate_start),
            "overlap_to": min(current_end, candidate_end),
            "participants": shared,
            "resources": shared_resources,
            "redacted": not visible,
        })
    return conflicts


def _meeting_payload(db: Session, item: Meeting, actor: User) -> dict:
    participant_refs = _meeting_participant_payloads(db, item.id)
    resource_refs = _meeting_resource_payloads(db, item.id)
    conflicts = _meeting_conflicts(db, item, actor)
    participant_count = len(participant_refs)
    resource_warnings = [
        {
            "code": "capacity_exceeded",
            "resource_id": resource["id"],
            "resource_name": resource["name"],
            "capacity": resource["capacity"],
            "participant_count": participant_count,
        }
        for resource in resource_refs
        if resource["capacity"] is not None and participant_count > resource["capacity"]
    ]
    membership = None if actor.is_admin else db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == item.project_id,
        ProjectMember.user_id == actor.id,
    ))
    role = "owner" if actor.is_admin else (membership.role if membership is not None else "viewer")
    return {
        "id": item.id, "record_version": item.record_version,
        "project_id": item.project_id, "contract_id": item.contract_id,
        "title": item.title, "scheduled_at": item.scheduled_at,
        "duration_minutes": item.duration_minutes, "participants": item.participants,
        "participant_user_ids": [row["id"] for row in participant_refs if row["kind"] == "user"],
        "participant_contact_ids": [row["id"] for row in participant_refs if row["kind"] == "contact"],
        "participant_refs": participant_refs,
        "resource_ids": [row["id"] for row in resource_refs], "resource_refs": resource_refs,
        "agenda": item.agenda, "minutes": item.minutes, "status": item.status,
        "has_conflicts": bool(conflicts), "conflict_count": len(conflicts), "conflicts": conflicts,
        "has_resource_warnings": bool(resource_warnings), "resource_warnings": resource_warnings,
        "can_edit": actor.is_admin or role in {"owner", "manager", "editor"},
        "can_manage": actor.is_admin or role in {"owner", "manager"},
    }


@router.get("/attention")
def attention_feed(
    project_id: int | None = None,
    contract_id: int | None = None,
    owner_user_id: int | None = None,
    kind: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Permission-filtered MVP-3 attention queue; no lifecycle state is duplicated here."""
    return build_attention_feed(
        db, user, meeting_payload=_meeting_payload,
        project_id=project_id, contract_id=contract_id, owner_user_id=owner_user_id,
        kind=kind, status=status, date_from=date_from, date_to=date_to,
        cursor=cursor, limit=limit,
    )


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
    return {"meetings": [_meeting_payload(db, row, user) for row in rows], "count": len(rows),
            "next_cursor": rows[-1].id if has_more and rows else None}


@router.post("/meetings")
def create_meeting(payload: MeetingCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if payload.contract_id is not None and not db.scalar(select(Contract.id).where(Contract.id == payload.contract_id, Contract.project_id == payload.project_id)):
        raise HTTPException(422, "Договор не принадлежит выбранному проекту")
    member_ids = set(db.scalars(select(ProjectMember.user_id).where(
        ProjectMember.project_id == payload.project_id,
        ProjectMember.user_id.in_(payload.participant_user_ids),
    ))) if payload.participant_user_ids else set()
    if member_ids != set(payload.participant_user_ids):
        raise HTTPException(422, "Участник-пользователь должен состоять в проекте")
    contact_ids = set(db.scalars(select(ProjectContact.id).where(
        ProjectContact.project_id == payload.project_id,
        ProjectContact.id.in_(payload.participant_contact_ids),
        ProjectContact.active.is_(True),
    ))) if payload.participant_contact_ids else set()
    if contact_ids != set(payload.participant_contact_ids):
        raise HTTPException(422, "Контакт должен быть активным и относиться к проекту")
    selected_resources = list(db.scalars(
        select(BookableResource)
        .where(BookableResource.id.in_(payload.resource_ids))
        .order_by(BookableResource.id)
        .with_for_update()
    )) if payload.resource_ids else []
    if (
        {resource.id for resource in selected_resources} != set(payload.resource_ids)
        or any(resource.organization_id != project.organization_id or not resource.active
               for resource in selected_resources)
    ):
        raise HTTPException(422, "Ресурс должен быть активным и относиться к организации проекта")
    data = payload.model_dump(exclude={"participant_user_ids", "participant_contact_ids", "resource_ids"})
    item = Meeting(**data, created_by_user_id=user.id)
    db.add(item); db.flush()
    db.add_all([
        *(MeetingParticipant(meeting_id=item.id, user_id=user_id) for user_id in payload.participant_user_ids),
        *(MeetingParticipant(meeting_id=item.id, contact_id=contact_id) for contact_id in payload.participant_contact_ids),
        *(MeetingResource(meeting_id=item.id, resource_id=resource_id) for resource_id in payload.resource_ids),
    ])
    db.flush()
    conflicts = _meeting_conflicts(db, item, user)
    append_management_history(db, project_id=item.project_id, entity_type="meeting", entity_id=item.id,
                              record_version=item.record_version, action="created", actor_user_id=user.id,
                              old_values={}, new_values={"title": item.title, "status": item.status,
                                                         "scheduled_at": item.scheduled_at,
                                                         "duration_minutes": item.duration_minutes},
                              evidence={"participant_user_ids": payload.participant_user_ids,
                                        "participant_contact_ids": payload.participant_contact_ids,
                                        "resource_ids": payload.resource_ids,
                                        "conflicting_meeting_ids": [row["meeting_id"] for row in conflicts
                                                                    if row["meeting_id"] is not None]},
                              reason=item.agenda)
    db.add(AuditLog(action="meeting_created", entity_type="meeting", entity_id=item.id, details=f"user={user.id}"))
    db.commit(); db.refresh(item)
    return _meeting_payload(db, item, user)


@router.patch("/meetings/{meeting_id}")
def finish_meeting(meeting_id: int, payload: MeetingUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = _locked_versioned(db, Meeting, meeting_id, payload.expected_record_version, "Meeting")
    require_project_role(db, user, item.project_id, "editor")
    old = {"minutes": item.minutes, "status": item.status}
    item.minutes, item.status = payload.minutes.strip(), payload.status
    item.record_version += 1
    db.flush()
    append_management_history(db, project_id=item.project_id, entity_type="meeting", entity_id=item.id,
                              record_version=item.record_version, action="minutes_recorded", actor_user_id=user.id,
                              old_values=old, new_values={"minutes": item.minutes, "status": item.status},
                              evidence={"proposal_state": "source_binding_required"
                                        if payload.status == "completed" else "not_applicable"},
                              reason="Протокол сохранён; действия требуют точного источника и подтверждения")
    db.add(AuditLog(action="meeting_minutes_recorded", entity_type="meeting", entity_id=item.id,
                    details=f"status={item.status};direct_actions=0;user={user.id}"))
    db.commit()
    return {"id": item.id, "status": item.status, "record_version": item.record_version,
            "tasks": 0, "risks": 0, "decisions": 0, "proposals": 0,
            "proposal_state": "source_binding_required" if item.status == "completed" else "not_applicable"}


def _proposal_error(exc: RuntimeError) -> HTTPException:
    if isinstance(exc, MeetingProposalConflict):
        return HTTPException(409, {"code": str(exc)})
    return HTTPException(403, {"code": str(exc)})


@router.post("/meetings/{meeting_id}/source-binding")
def bind_meeting_source(meeting_id: int, payload: MeetingSourceBindingCreate,
                        db: Session = Depends(get_db), user: User = Depends(require_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    require_project_role(db, user, meeting.project_id, "manager")
    try:
        result = bind_current_source(
            db, meeting_id=meeting_id, actor_user_id=user.id,
            **payload.model_dump(),
        )
        db.commit()
        return result
    except (MeetingProposalConflict, MeetingProposalDenied) as exc:
        db.rollback()
        raise _proposal_error(exc) from None


@router.get("/meetings/{meeting_id}/proposals")
def meeting_proposals(meeting_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    require_project_role(db, user, meeting.project_id, "viewer")
    rows = list(db.scalars(select(MeetingProposal).where(
        MeetingProposal.meeting_id == meeting_id,
    ).order_by(MeetingProposal.id)))
    binding = db.get(MeetingSourceBinding, rows[0].binding_id) if rows else None
    version = db.get(SourceVersion, binding.source_version_id) if binding is not None else None
    locator = version.locator_at_observation if version is not None else None
    locator = locator if isinstance(locator, dict) else {}
    source_binding = None if binding is None else {
        "source_id": binding.source_id,
        "source_version_id": binding.source_version_id,
        "evidence_id": binding.evidence_id,
        "materialization_id": binding.materialization_id,
        "display_name": locator.get("display_name") or "Локальный документ",
        "media_type": locator.get("media_type"),
        "observed_at": version.observed_at if version is not None else None,
    }
    return {"proposals": [serialize_proposal(row) for row in rows], "count": len(rows),
            "source_binding": source_binding}


@router.get("/meetings/{meeting_id}/source-candidates")
def meeting_source_candidates(meeting_id: int, limit: int = 100,
                              db: Session = Depends(get_db),
                              user: User = Depends(require_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    require_project_role(db, user, meeting.project_id, "manager")
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be between 1 and 200")
    try:
        candidates = list_current_local_upload_sources(
            db, actor_user_id=user.id, project_id=meeting.project_id, limit=limit,
        )
    except MeetingSourceDenied:
        raise HTTPException(403, "resource_unavailable") from None
    return {"candidates": candidates, "count": len(candidates)}


@router.post("/meeting-proposals/{proposal_id}/confirm")
def confirm_meeting_proposal(proposal_id: int, payload: MeetingProposalConfirm,
                             db: Session = Depends(get_db), user: User = Depends(require_user)):
    proposal = db.get(MeetingProposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, "Meeting proposal not found")
    require_project_role(db, user, proposal.project_id, "manager")
    try:
        result = confirm_proposal(
            db, proposal_id=proposal_id, actor_user_id=user.id,
            **payload.model_dump(),
        )
        db.commit()
        return result
    except (MeetingProposalConflict, MeetingProposalDenied) as exc:
        db.rollback()
        raise _proposal_error(exc) from None


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
    refresh_notifications_for_user(project_id, db, user)
    return list_notifications(project_id, db=db, user=user)


def refresh_notifications_for_user(project_id: int, db: Session, user: User) -> None:
    """Materialize one user's notifications without depending on an HTTP request."""
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
