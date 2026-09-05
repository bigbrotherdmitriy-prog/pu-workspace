"""Fail-closed meeting proposals and internal management digests.

This module never parses or stores raw meeting/message content.  A caller may
submit structured candidates only after producing immutable Evidence records.
The durable objects remain proposals until a manager confirms them.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.jobs import queue
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.governance import Decision
from app.models.management import Meeting, Notification, Obligation
from app.models.v54_pilot import Evidence, SourceReference
from app.mvp3.attention import attention_page
from app.mvp3.lifecycle import ManagementConflict, ManagementDenied, ManagementLifecycle


class MeetingActionCandidate(BaseModel):
    """Structured extraction result; raw source content is deliberately absent."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["obligation", "task", "decision"]
    title: str = Field(min_length=2, max_length=500)
    owner_user_id: int = Field(gt=0)
    evidence_pins: list[dict] = Field(min_length=1, max_length=20)
    due_date: date | None = None
    due_time: time | None = None
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=100)


class DigestPreference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    timezone: str = Field(min_length=1, max_length=100)
    quiet_start: time
    quiet_end: time
    channel: Literal["in_app", "disabled"]

    @model_validator(mode="after")
    def validate_timezone(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("invalid_timezone") from exc
        return self


class _DigestJobPayload(BaseModel):
    # JSON transports dates and times as strings; validation remains closed to
    # unknown keys while parsing only the declared scalar fields.
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    timezone: str
    quiet_start: time
    quiet_end: time
    channel: Literal["in_app", "disabled"]
    local_date: date


class MeetingProposalService:
    """DB-only proposal lifecycle; transaction ownership stays with the caller."""

    def __init__(self, lifecycle: ManagementLifecycle | None = None):
        self.lifecycle = lifecycle or ManagementLifecycle()

    def propose(self, db: Session, *, project_id: int, meeting_id: int, actor_user_id: int,
                candidates: list[MeetingActionCandidate]) -> list[dict]:
        scope = self.lifecycle.scope(db, project_id=project_id, actor_user_id=actor_user_id)
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or meeting.project_id != project_id or meeting.status != "completed":
            raise ManagementDenied("resource_unavailable")
        return self._propose(db, scope=scope, origin_type="meeting", origin_id=meeting.id,
                             candidates=candidates)

    def propose_message(self, db: Session, *, project_id: int, message_id: int, actor_user_id: int,
                        candidates: list[MeetingActionCandidate]) -> list[dict]:
        scope = self.lifecycle.scope(db, project_id=project_id, actor_user_id=actor_user_id)
        message = db.get(Message, message_id)
        if (message is None or message.project_id != project_id or not message.context_confirmed
                or not message.source_reference_id):
            raise ManagementDenied("resource_unavailable")
        for candidate in candidates:
            for raw_pin in candidate.evidence_pins:
                evidence_id = (((raw_pin.get("ref") or {}).get("id") or {}).get("value"))
                evidence = db.get(Evidence, evidence_id) if evidence_id else None
                source = db.get(SourceReference, evidence.source_id) if evidence else None
                if source is None or not (
                    source.id == message.source_reference_id or source.parent_source_id == message.source_reference_id
                ):
                    raise ManagementDenied("resource_unavailable")
        return self._propose(db, scope=scope, origin_type="message", origin_id=message.id,
                             candidates=candidates)

    def _propose(self, db: Session, *, scope, origin_type: str, origin_id: int,
                 candidates: list[MeetingActionCandidate]) -> list[dict]:
        if not candidates or len(candidates) > 100:
            raise ManagementDenied("invalid_input")

        result: list[dict] = []
        for candidate in candidates:
            # Validate and canonicalize before deriving the existing lifecycle key.
            pins, _, _ = self.lifecycle.evidence(db, scope, candidate.evidence_pins)
            if candidate.kind in {"obligation", "task"}:
                digest = hashlib.sha256(repr(pins).encode()).hexdigest()
                row = db.scalar(select(Obligation).where(
                    Obligation.project_id == scope.project_id, Obligation.source_hash == digest,
                ))
                created = row is None
                if row is None:
                    row = self.lifecycle.create_obligation(
                        db, scope=scope, title=candidate.title, owner_user_id=candidate.owner_user_id,
                        evidence_pins=pins, due_date=candidate.due_date, due_time=candidate.due_time,
                        timezone_name=candidate.timezone,
                    )
                elif (row.title != candidate.title.strip() or row.owner_user_id != candidate.owner_user_id
                      or row.due_date != candidate.due_date or row.due_time != candidate.due_time
                      or row.evidence_pins != pins):
                    raise ManagementDenied("evidence_already_bound")
                result.append(self._proposal(candidate.kind, "obligation", row))
            else:
                digest = hashlib.sha256(("decision:" + repr(pins) + candidate.title).encode()).hexdigest()
                row = db.scalar(select(Decision).where(
                    Decision.project_id == scope.project_id, Decision.source_hash == digest,
                ))
                created = row is None
                if row is None:
                    row = self.lifecycle.create_decision(
                        db, scope=scope, question=candidate.title, owner_user_id=candidate.owner_user_id,
                        evidence_pins=pins,
                    )
                elif (row.question != candidate.title.strip() or row.owner_user_id != candidate.owner_user_id
                      or row.evidence_pins != pins):
                    raise ManagementDenied("evidence_already_bound")
                result.append(self._proposal("decision", "decision", row))
            if created:
                # Identifiers and counts only: no title, minutes, message or evidence content.
                db.add(AuditLog(action="mvp3_proposal_created", entity_type=origin_type,
                                entity_id=origin_id,
                                details=f"proposal_type={candidate.kind};proposal_id={row.id}"))
        return result

    def confirm(self, db: Session, *, project_id: int, actor_user_id: int, entity_type: str,
                entity_id: int, expected_version: int, create_internal_task: bool = False) -> dict:
        # Confirmation is deliberately stricter than ordinary proposal creation.
        scope = self.lifecycle.scope(db, project_id=project_id, actor_user_id=actor_user_id, minimum="manager")
        if entity_type == "obligation":
            row = db.get(Obligation, entity_id)
            if row is None or row.project_id != project_id:
                raise ManagementDenied("resource_unavailable")
            if row.status == "needs_confirmation":
                row = self.lifecycle.transition_obligation(
                    db, scope=scope, obligation_id=row.id, expected_version=expected_version, status="confirmed",
                )
            elif row.status not in {"confirmed", "in_progress"}:
                raise ManagementDenied("resource_unavailable")
            if create_internal_task:
                row = db.get(Obligation, row.id)
                self.lifecycle.ensure_internal_task(
                    db, scope=scope, obligation_id=row.id, expected_version=row.record_version,
                )
                row = db.get(Obligation, row.id)
            return self._proposal("task" if create_internal_task else "obligation", "obligation", row)
        if entity_type == "decision" and not create_internal_task:
            row = db.get(Decision, entity_id)
            if row is None or row.project_id != project_id:
                raise ManagementDenied("resource_unavailable")
            if row.status == "needs_confirmation":
                row = self.lifecycle.transition_governance(
                    db, scope=scope, entity_type="decision", entity_id=row.id,
                    expected_version=expected_version, status="confirmed",
                )
            elif row.status != "confirmed":
                raise ManagementDenied("resource_unavailable")
            return self._proposal("decision", "decision", row)
        raise ManagementDenied("resource_unavailable")

    @staticmethod
    def _proposal(kind: str, entity_type: str, row) -> dict:
        return {"kind": kind, "entity_type": entity_type, "entity_id": row.id,
                "record_version": row.record_version, "status": row.status,
                "review_state": row.review_state,
                "task_id": row.task_id if entity_type == "obligation" else None}


class MeetingDigestService:
    """Creates one aggregate, content-free in-app digest per local day."""

    def __init__(self, lifecycle: ManagementLifecycle | None = None):
        self.lifecycle = lifecycle or ManagementLifecycle()

    def generate(self, db: Session, *, project_id: int, user_id: int, preference: DigestPreference,
                 now: datetime | None = None, expected_local_date: date | None = None) -> dict:
        self.lifecycle.scope(db, project_id=project_id, actor_user_id=user_id, minimum="viewer")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("timezone_required")
        local = current.astimezone(ZoneInfo(preference.timezone))
        if expected_local_date is not None and local.date() != expected_local_date:
            return self._result("stale", local_date=expected_local_date)
        if preference.channel == "disabled":
            return self._result("disabled", local_date=local.date())
        if self._is_quiet(local.time().replace(tzinfo=None), preference.quiet_start, preference.quiet_end):
            return self._result("deferred_quiet_hours", local_date=local.date(),
                                deferred_until=self._quiet_end(local, preference).isoformat())

        digest_key = f"mvp3:digest:{project_id}:{user_id}:{local.date().isoformat()}:in_app"
        existing = db.scalar(select(Notification).where(
            Notification.user_id == user_id, Notification.dedupe_key == digest_key,
        ))
        if existing is not None:
            return self._result("already_created", local_date=local.date(), notification_id=existing.id)

        attention = attention_page(db, project_id=project_id, now=current, limit=100)
        counts: dict[str, int] = {}
        for item in attention["items"]:
            counts[item["entity_type"]] = counts.get(item["entity_type"], 0) + 1
        total = attention["total"]
        if total == 0:
            return self._result("empty", local_date=local.date(), counts=counts)
        row = Notification(
            project_id=project_id, user_id=user_id, kind="management_digest",
            title="Сводка управления проектом",
            body=f"Требуют внимания: {total}. Откройте центр управления для проверки.",
            entity_type="project", entity_id=project_id, dedupe_key=digest_key,
        )
        db.add(row)
        db.flush()
        return self._result("created", local_date=local.date(), notification_id=row.id, counts=counts)

    @staticmethod
    def _is_quiet(value: time, start: time, end: time) -> bool:
        if start == end:
            return True
        return start <= value < end if start < end else value >= start or value < end

    @staticmethod
    def _quiet_end(local: datetime, preference: DigestPreference) -> datetime:
        target = local.replace(hour=preference.quiet_end.hour, minute=preference.quiet_end.minute,
                               second=preference.quiet_end.second, microsecond=0)
        if preference.quiet_start >= preference.quiet_end and local.time().replace(tzinfo=None) >= preference.quiet_start:
            target += timedelta(days=1)
        elif preference.quiet_start < preference.quiet_end and target <= local:
            target += timedelta(days=1)
        return target

    @staticmethod
    def _result(status: str, *, local_date: date, **extra) -> dict:
        return {"status": status, "local_date": local_date.isoformat(),
                "external_actions_created": False, **extra}


def enqueue_digest(db: Session, *, project_id: int, user_id: int, actor_user_id: int,
                   preference: DigestPreference, local_date: date):
    ManagementLifecycle().scope(db, project_id=project_id, actor_user_id=actor_user_id, minimum="viewer")
    if actor_user_id != user_id:
        raise ManagementDenied("resource_unavailable")
    payload = {
        "project_id": project_id, "user_id": user_id, "timezone": preference.timezone,
        "quiet_start": preference.quiet_start.isoformat(), "quiet_end": preference.quiet_end.isoformat(),
        "channel": preference.channel, "local_date": local_date.isoformat(),
    }
    return queue.enqueue(
        db, "mvp3.management_digest", payload, priority=200, max_attempts=3,
        idempotency_key=f"mvp3.digest:{project_id}:{user_id}:{local_date.isoformat()}:{preference.channel}",
    )


_session_factory: Callable[[], Session] = SessionLocal
_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def install_digest_runtime(session_factory: Callable[[], Session] | None = None,
                           *, clock: Callable[[], datetime] | None = None) -> None:
    """Narrow installation seam for worker and synthetic tests."""
    global _session_factory, _clock
    _session_factory = session_factory or SessionLocal
    _clock = clock or (lambda: datetime.now(timezone.utc))


def run_digest_job(payload: dict) -> dict:
    try:
        parsed = _DigestJobPayload.model_validate(payload)
        preference = DigestPreference(
            timezone=parsed.timezone, quiet_start=parsed.quiet_start,
            quiet_end=parsed.quiet_end, channel=parsed.channel,
        )
    except ValueError as exc:
        raise ValueError("invalid_job_payload") from exc
    session = _session_factory()
    factory = _session_factory
    try:
        result = MeetingDigestService().generate(
            session, project_id=parsed.project_id, user_id=parsed.user_id,
            preference=preference, now=_clock(), expected_local_date=parsed.local_date,
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        # A custom test factory may supply an externally owned fixture Session.
        if factory is SessionLocal:
            session.close()
