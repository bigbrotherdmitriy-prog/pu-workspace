"""Durable, content-minimized MVP-3 management digests.

The scheduler transports identifiers only.  The worker re-checks the current
project membership and policy, then persists one immutable receipt per local
day.  Receipts contain bounded identifiers/evidence pins, never raw source
content, and never perform external actions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.jobs.queue import enqueue
from app.models.governance import Decision, Risk
from app.models.job import BackgroundJob
from app.models.management import (
    ManagementDigest, ManagementHistory, MeetingProposal, MeetingSourceBinding,
    Notification, NotificationPolicy, Obligation,
)
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.notification_escalation import outside_quiet_hours, require_iana_timezone

DIGEST_KIND = "mvp3.management_digest"
MAX_DIGEST_ITEMS = 100
ACTIVE_OBLIGATION_STATUSES = ("confirmed", "in_progress")
ACTIVE_RISK_STATUSES = ("confirmed", "mitigating")


class DigestPayload(BaseModel):
    # Queue JSON transports ISO dates as strings; unknown fields still fail
    # closed while Pydantic performs the one declared date conversion.
    model_config = ConfigDict(extra="forbid")

    organization_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    policy_id: int = Field(gt=0)
    policy_record_version: int = Field(gt=0)
    local_date: date


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _inside_quiet_hours(now: datetime, policy: NotificationPolicy) -> bool:
    return outside_quiet_hours(
        now, timezone_name=policy.timezone,
        quiet_start=policy.quiet_start, quiet_end=policy.quiet_end,
    ) > now.astimezone(timezone.utc)


def _policy_due(policy: NotificationPolicy, now: datetime) -> tuple[bool, date]:
    local = now.astimezone(require_iana_timezone(policy.timezone))
    if not policy.digest_enabled or not policy.enabled:
        return False, local.date()
    if policy.digest_cadence == "weekdays" and local.weekday() >= 5:
        return False, local.date()
    if local.time().replace(tzinfo=None) < policy.digest_local_time:
        return False, local.date()
    if _inside_quiet_hours(now, policy):
        return False, local.date()
    return True, local.date()


def schedule_digest_jobs(db: Session, *, now: datetime | None = None) -> int:
    """Enqueue due policy versions once per local day.

    Only identifiers cross the durable queue boundary.  ``enqueue`` and its
    unique key make concurrent scheduler passes safe.
    """
    current = _aware(now or datetime.now(timezone.utc))
    rows = db.scalars(
        select(NotificationPolicy)
        .join(Project, Project.id == NotificationPolicy.project_id)
        .join(
            ProjectMember,
            (ProjectMember.project_id == NotificationPolicy.project_id)
            & (ProjectMember.user_id == NotificationPolicy.user_id),
        )
        .where(Project.archived_at.is_(None))
        .order_by(NotificationPolicy.id)
    ).all()
    created = 0
    for policy in rows:
        due, local_date = _policy_due(policy, current)
        if not due:
            continue
        key = f"mvp3-digest:{policy.id}:v{policy.record_version}:{local_date.isoformat()}"
        existed = db.scalar(select(ManagementDigest.id).where(
            ManagementDigest.policy_id == policy.id,
            ManagementDigest.local_date == local_date,
        ))
        if existed is not None:
            continue
        if db.scalar(select(BackgroundJob.id).where(BackgroundJob.idempotency_key == key)) is not None:
            continue
        job = enqueue(
            db, DIGEST_KIND,
            {
                "organization_id": policy.organization_id,
                "project_id": policy.project_id,
                "user_id": policy.user_id,
                "policy_id": policy.id,
                "policy_record_version": policy.record_version,
                "local_date": local_date.isoformat(),
            },
            priority=180, max_attempts=5, idempotency_key=key,
        )
        created += int(job.status == "queued" and job.attempts == 0)
    return created


def _proposal_refs(db: Session, project_id: int) -> tuple[list[dict], set[tuple[str, int]]]:
    result: list[dict] = []
    targets: set[tuple[str, int]] = set()
    rows = db.execute(
        select(MeetingProposal, MeetingSourceBinding)
        .join(MeetingSourceBinding, MeetingSourceBinding.id == MeetingProposal.binding_id)
        .where(
            MeetingProposal.project_id == project_id,
            MeetingProposal.status == "confirmed",
            MeetingProposal.target_entity_id.is_not(None),
        )
        .order_by(MeetingProposal.id)
        .limit(MAX_DIGEST_ITEMS + 1)
    ).all()
    for proposal, binding in rows[:MAX_DIGEST_ITEMS]:
        entity_type = str(proposal.target_entity_type)
        entity_id = int(proposal.target_entity_id)
        status = None
        record_version = 1
        active = False
        if entity_type == "task":
            row = db.get(Task, entity_id)
            active = row is not None and row.project_id == project_id and row.status not in {"completed", "cancelled"}
            status = row.status if row is not None else None
            record_version = row.record_version if row is not None else 1
        elif entity_type == "risk":
            row = db.get(Risk, entity_id)
            active = row is not None and row.project_id == project_id and row.status in ACTIVE_RISK_STATUSES
            status = row.status if row is not None else None
            record_version = row.record_version if row is not None else 1
        elif entity_type == "decision":
            row = db.get(Decision, entity_id)
            active = row is not None and row.project_id == project_id and row.status == "confirmed"
            status = row.status if row is not None else None
            record_version = row.record_version if row is not None else 1
        if not active:
            continue
        targets.add((entity_type, entity_id))
        result.append({
            "entity_type": entity_type,
            "entity_id": entity_id,
            "record_version": record_version,
            "status": status,
            "proposal_origin": {
                "proposal_id": proposal.id,
                "meeting_id": proposal.meeting_id,
                "binding_id": binding.id,
                "source_id": binding.source_id,
                "source_version_id": binding.source_version_id,
                "evidence_id": binding.evidence_id,
                "materialization_id": binding.materialization_id,
            },
        })
    return result, targets


def _direct_refs(db: Session, project_id: int, excluded: set[tuple[str, int]], limit: int) -> list[dict]:
    result: list[dict] = []
    specs = (
        ("obligation", Obligation, Obligation.status.in_(ACTIVE_OBLIGATION_STATUSES)),
        ("risk", Risk, Risk.status.in_(ACTIVE_RISK_STATUSES)),
        ("decision", Decision, Decision.status == "confirmed"),
    )
    for entity_type, model, active_clause in specs:
        if len(result) >= limit:
            break
        rows = db.scalars(select(model).where(
            model.project_id == project_id, active_clause,
        ).order_by(model.id).limit(limit - len(result))).all()
        for row in rows:
            if (entity_type, row.id) in excluded:
                continue
            result.append({
                "entity_type": entity_type,
                "entity_id": row.id,
                "record_version": row.record_version,
                "status": row.status,
                "source": {
                    "source_type": row.source_type,
                    "source_id": row.source_id,
                    "source_hash": row.source_hash,
                },
            })
            if len(result) >= limit:
                break
    return result


def _digest_refs(db: Session, project_id: int) -> list[dict]:
    proposals, targets = _proposal_refs(db, project_id)
    return proposals + _direct_refs(db, project_id, targets, MAX_DIGEST_ITEMS - len(proposals))


def _run(db: Session, payload: DigestPayload, *, now: datetime) -> dict:
    policy = db.scalar(select(NotificationPolicy).where(
        NotificationPolicy.id == payload.policy_id,
    ).with_for_update())
    project = db.get(Project, payload.project_id)
    member = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == payload.project_id,
        ProjectMember.user_id == payload.user_id,
    ))
    if (
        policy is None or project is None or member is None or project.archived_at is not None
        or project.organization_id != payload.organization_id
        or policy.organization_id != payload.organization_id
        or policy.project_id != payload.project_id or policy.user_id != payload.user_id
    ):
        return {"status": "scope_revoked", "external_actions_created": False}
    if policy.record_version != payload.policy_record_version:
        return {"status": "stale_policy", "external_actions_created": False}
    due, local_date = _policy_due(policy, now)
    if local_date != payload.local_date:
        return {"status": "stale_day", "external_actions_created": False}
    if not due:
        status = "disabled" if not policy.enabled or not policy.digest_enabled else "deferred"
        return {"status": status, "external_actions_created": False}
    existing = db.scalar(select(ManagementDigest).where(
        ManagementDigest.policy_id == policy.id,
        ManagementDigest.local_date == payload.local_date,
    ))
    if existing is not None:
        return {
            "status": "already_created", "digest_id": existing.id,
            "notification_id": existing.notification_id,
            "item_count": existing.item_count, "external_actions_created": False,
        }

    refs = _digest_refs(db, policy.project_id)
    if not refs:
        return {"status": "empty", "item_count": 0, "external_actions_created": False}
    counts: dict[str, int] = {}
    for item in refs:
        counts[item["entity_type"]] = counts.get(item["entity_type"], 0) + 1
    notification = Notification(
        project_id=policy.project_id, user_id=policy.user_id,
        kind="management_digest", title="Сводка управления проектом",
        body=f"Требуют внимания: {len(refs)}. Откройте центр управления для проверки.",
        entity_type="project", entity_id=policy.project_id,
        dedupe_key=f"management-digest:{policy.id}:{payload.local_date.isoformat()}",
    )
    db.add(notification); db.flush()
    digest = ManagementDigest(
        organization_id=policy.organization_id, project_id=policy.project_id,
        user_id=policy.user_id, policy_id=policy.id,
        policy_record_version=policy.record_version, local_date=payload.local_date,
        notification_id=notification.id, item_count=len(refs), item_refs=refs,
        requested_channels=list(policy.channels or []),
    )
    db.add(digest); db.flush()
    db.add(ManagementHistory(
        organization_id=policy.organization_id, project_id=policy.project_id,
        entity_type="management_digest", entity_id=digest.id, record_version=1,
        action="created", actor_user_id=policy.user_id,
        old_values={}, new_values={"local_date": payload.local_date.isoformat(),
                                   "item_count": len(refs), "counts": counts},
        evidence={"item_refs": refs},
        reason="Автоматическая сводка по сохранённой политике уведомлений",
    ))
    return {
        "status": "created", "digest_id": digest.id,
        "notification_id": notification.id, "item_count": len(refs),
        "counts": counts, "external_actions_created": False,
    }


_session_factory: Callable[[], Session] = SessionLocal
_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def install_digest_runtime(session_factory: Callable[[], Session] | None = None,
                           *, clock: Callable[[], datetime] | None = None) -> None:
    global _session_factory, _clock
    _session_factory = session_factory or SessionLocal
    _clock = clock or (lambda: datetime.now(timezone.utc))


def run_digest_job(raw_payload: dict) -> dict:
    try:
        payload = DigestPayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise ValueError("invalid_management_digest_payload") from exc
    db = _session_factory()
    owned = _session_factory is SessionLocal
    try:
        result = _run(db, payload, now=_aware(_clock()))
        db.commit()
        return result
    except IntegrityError:
        # A concurrent worker or a replay may win the immutable day receipt.
        db.rollback()
        existing = db.scalar(select(ManagementDigest).where(
            ManagementDigest.policy_id == payload.policy_id,
            ManagementDigest.local_date == payload.local_date,
        ))
        if existing is None:
            raise
        return {
            "status": "already_created", "digest_id": existing.id,
            "notification_id": existing.notification_id,
            "item_count": existing.item_count, "external_actions_created": False,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        if owned:
            db.close()
