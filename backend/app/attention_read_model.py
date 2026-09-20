from __future__ import annotations

import base64
import binascii
import json
from datetime import date, datetime
from typing import Callable, Iterable

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role
from app.models.governance import Decision, Risk
from app.models.management import Meeting, MeetingProposal, MeetingSourceBinding, Notification, Obligation
from app.models.project import Project
from app.models.project_contact import ContactConflict, ProjectContact
from app.models.project_member import ProjectMember
from app.models.user import User


PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2}
ATTENTION_KINDS = frozenset({
    "obligation", "risk", "decision", "notification", "meeting_proposal",
    "contact_conflict", "meeting_conflict",
})


def _csv_filter(value: str | None) -> set[str] | None:
    if value is None:
        return None
    result = {part.strip() for part in value.split(",") if part.strip()}
    return result or None


def _encode_cursor(key: tuple) -> str:
    payload = json.dumps(list(key), ensure_ascii=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(decoded, list) or len(decoded) != 5:
            raise ValueError
        priority, effective, kind, entity_id, discriminator = decoded
        if not isinstance(priority, int) or not isinstance(effective, str) or not isinstance(kind, str):
            raise ValueError
        if not isinstance(entity_id, int) or not isinstance(discriminator, str):
            raise ValueError
        return priority, effective, kind, entity_id, discriminator
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(422, "Invalid attention cursor") from None


def _iso_date(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _sort_key(item: dict) -> tuple:
    effective = item["effective_date"] or "9999-12-31"
    return (
        PRIORITY_ORDER[item["priority"]], effective, item["kind"], item["entity_id"],
        item.get("discriminator") or "",
    )


def _target(section: str, project_id: int, entity_type: str, entity_id: int) -> dict:
    return {
        "section": section,
        "project_id": project_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


def _source_origin(row) -> dict:
    return {
        "type": row.source_type,
        "id": row.source_id,
        "name": row.source_name,
        "excerpt": row.source_excerpt,
        "hash": row.source_hash,
    }


def _accessible_project_ids(db: Session, user: User, project_id: int | None) -> list[int]:
    if project_id is not None:
        require_project_role(db, user, project_id, "viewer")
        return [project_id]
    query = select(Project.id).where(Project.archived_at.is_(None)).order_by(Project.id)
    if not user.is_admin:
        query = query.join(ProjectMember).where(ProjectMember.user_id == user.id)
    return list(db.scalars(query))


def _meeting_conflict_items(
    db: Session,
    user: User,
    project_ids: list[int],
    meeting_payload: Callable[[Session, Meeting, User], dict],
) -> Iterable[dict]:
    meetings = list(db.scalars(select(Meeting).where(
        Meeting.project_id.in_(project_ids),
        Meeting.status != "cancelled",
        Meeting.scheduled_at.is_not(None),
    ).order_by(Meeting.id)))
    for meeting in meetings:
        payload = meeting_payload(db, meeting, user)
        conflicts = payload.get("conflicts") or []
        resource_warnings = payload.get("resource_warnings") or []
        if not conflicts and not resource_warnings:
            continue
        redacted_count = sum(1 for conflict in conflicts if conflict.get("redacted"))
        reasons = []
        if conflicts:
            reasons.append(f"Пересечений по времени: {len(conflicts)}")
        if resource_warnings:
            reasons.append(f"Предупреждений вместимости: {len(resource_warnings)}")
        yield {
            "kind": "meeting_conflict",
            "entity_id": meeting.id,
            "discriminator": "schedule",
            "status": "unresolved",
            "priority": "high",
            "project_id": meeting.project_id,
            "contract_id": meeting.contract_id,
            "owner_user_id": meeting.created_by_user_id,
            "title": meeting.title,
            "effective_date": _iso_date(meeting.scheduled_at),
            "explanation": "; ".join(reasons),
            "origin": {
                "type": "meeting_schedule",
                "id": str(meeting.id),
                "scheduled_at": meeting.scheduled_at,
                "duration_minutes": meeting.duration_minutes,
                "conflict_count": len(conflicts),
                "redacted_conflict_count": redacted_count,
            },
            "navigation": _target("Совещания", meeting.project_id, "meeting", meeting.id),
        }


def build_attention_feed(
    db: Session,
    user: User,
    *,
    meeting_payload: Callable[[Session, Meeting, User], dict],
    project_id: int | None = None,
    contract_id: int | None = None,
    owner_user_id: int | None = None,
    kind: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be between 1 and 200")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    kinds = _csv_filter(kind)
    if kinds and not kinds <= ATTENTION_KINDS:
        raise HTTPException(422, "Unsupported attention kind")
    statuses = _csv_filter(status)
    project_ids = _accessible_project_ids(db, user, project_id)
    if not project_ids:
        return {"items": [], "count": 0, "next_cursor": None}

    items: list[dict] = []
    if kinds is None or "obligation" in kinds:
        rows = db.scalars(select(Obligation).where(
            Obligation.project_id.in_(project_ids),
            Obligation.status.in_(["needs_confirmation", "confirmed", "in_progress", "breached"]),
        )).all()
        for row in rows:
            items.append({
                "kind": "obligation", "entity_id": row.id, "discriminator": "",
                "status": row.status,
                "priority": "critical" if row.due_date and row.due_date < date.today() else "high",
                "project_id": row.project_id, "contract_id": row.contract_id,
                "owner_user_id": row.owner_user_id, "title": row.title,
                "effective_date": _iso_date(row.due_date),
                "explanation": "Обязательство требует подтверждения или завершения",
                "origin": _source_origin(row),
                "navigation": _target("Обязательства", row.project_id, "obligation", row.id),
            })
    if kinds is None or "risk" in kinds:
        rows = db.scalars(select(Risk).where(
            Risk.project_id.in_(project_ids),
            Risk.status.in_(["needs_confirmation", "confirmed", "mitigating"]),
        )).all()
        for row in rows:
            items.append({
                "kind": "risk", "entity_id": row.id, "discriminator": "",
                "status": row.status,
                "priority": "critical" if row.criticality == "critical" else "high",
                "project_id": row.project_id, "contract_id": None,
                "owner_user_id": row.owner_user_id, "title": row.title,
                "effective_date": _iso_date(row.updated_at or row.created_at),
                "explanation": f"Открытый риск: критичность {row.criticality}",
                "origin": _source_origin(row),
                "navigation": _target("Риски и решения", row.project_id, "risk", row.id),
            })
    if kinds is None or "decision" in kinds:
        rows = db.scalars(select(Decision).where(
            Decision.project_id.in_(project_ids),
            Decision.status.in_(["needs_confirmation", "confirmed"]),
        )).all()
        for row in rows:
            items.append({
                "kind": "decision", "entity_id": row.id, "discriminator": "",
                "status": row.status, "priority": "high",
                "project_id": row.project_id, "contract_id": None,
                "owner_user_id": row.initiator_user_id, "title": row.question,
                "effective_date": _iso_date(row.updated_at or row.created_at),
                "explanation": "Решение ожидает подтверждения или фиксации результата",
                "origin": _source_origin(row),
                "navigation": _target("Риски и решения", row.project_id, "decision", row.id),
            })
    if kinds is None or "notification" in kinds:
        rows = db.scalars(select(Notification).where(
            Notification.project_id.in_(project_ids),
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        )).all()
        for row in rows:
            items.append({
                "kind": "notification", "entity_id": row.id, "discriminator": row.dedupe_key,
                "status": "unread", "priority": "critical" if row.kind == "overdue" else "normal",
                "project_id": row.project_id, "contract_id": None,
                "owner_user_id": row.user_id, "title": row.title,
                "effective_date": _iso_date(row.created_at),
                "explanation": row.body,
                "origin": {
                    "type": "notification", "id": str(row.id), "kind": row.kind,
                    "entity_type": row.entity_type, "entity_id": row.entity_id,
                    "dedupe_key": row.dedupe_key,
                },
                "navigation": _target("Уведомления", row.project_id, row.entity_type, row.entity_id),
            })
    if kinds is None or "meeting_proposal" in kinds:
        rows = db.execute(select(MeetingProposal, MeetingSourceBinding, Meeting)
            .join(MeetingSourceBinding, MeetingSourceBinding.id == MeetingProposal.binding_id)
            .join(Meeting, Meeting.id == MeetingProposal.meeting_id)
            .where(
                MeetingProposal.project_id.in_(project_ids),
                MeetingProposal.status == "proposed",
            )).all()
        for row, binding, meeting in rows:
            payload = row.payload or {}
            items.append({
                "kind": "meeting_proposal", "entity_id": row.id,
                "discriminator": row.proposal_type,
                "status": row.status, "priority": "high",
                "project_id": row.project_id, "contract_id": meeting.contract_id,
                "owner_user_id": row.created_by_user_id,
                "title": str(payload.get("title") or payload.get("question") or f"Предложение: {row.proposal_type}"),
                "effective_date": _iso_date(row.created_at),
                "explanation": "Предложение из протокола ожидает подтверждения manager",
                "origin": {
                    "type": "meeting_source", "id": binding.source_id,
                    "source_version_id": binding.source_version_id,
                    "evidence_id": binding.evidence_id,
                    "materialization_id": binding.materialization_id,
                    "meeting_id": row.meeting_id,
                },
                "navigation": _target("Совещания", row.project_id, "meeting_proposal", row.id),
            })
    if kinds is None or "contact_conflict" in kinds:
        rows = db.execute(select(ContactConflict, ProjectContact)
            .join(ProjectContact, ProjectContact.id == ContactConflict.contact_id)
            .where(
                or_(ContactConflict.current_project_id.in_(project_ids),
                    ContactConflict.candidate_project_id.in_(project_ids)),
                ContactConflict.status == "pending",
            )).all()
        project_id_set = set(project_ids)
        for row, contact in rows:
            visible_project_id = (row.candidate_project_id if row.candidate_project_id in project_id_set
                                  else row.current_project_id)
            contact_visible = contact.project_id in project_id_set
            items.append({
                "kind": "contact_conflict", "entity_id": row.id, "discriminator": "",
                "status": row.status, "priority": "high",
                "project_id": visible_project_id,
                "contract_id": contact.contract_id if contact_visible else None,
                "owner_user_id": None,
                "title": (f"Уточните проект контакта «{contact.name}»" if contact_visible
                          else "Уточните проект контакта из другого проекта"),
                "effective_date": _iso_date(row.created_at),
                "explanation": "Контакт обнаружен в контексте другого проекта; требуется решение пользователя",
                "origin": ({"type": "contact_conflict", "id": str(row.id), "contact_id": row.contact_id}
                           if contact_visible else {"type": "contact_conflict", "id": str(row.id),
                                                    "redacted": True}),
                "navigation": _target("Письма", visible_project_id, "contact_conflict", row.id),
            })
    if kinds is None or "meeting_conflict" in kinds:
        items.extend(_meeting_conflict_items(db, user, project_ids, meeting_payload))

    if contract_id is not None:
        items = [item for item in items if item["contract_id"] == contract_id]
    if owner_user_id is not None:
        items = [item for item in items if item["owner_user_id"] == owner_user_id]
    if statuses:
        items = [item for item in items if item["status"] in statuses]
    if date_from is not None:
        items = [item for item in items if item["effective_date"] is not None
                 and item["effective_date"] >= date_from.isoformat()]
    if date_to is not None:
        items = [item for item in items if item["effective_date"] is not None
                 and item["effective_date"] <= date_to.isoformat()]

    items.sort(key=_sort_key)
    cursor_key = _decode_cursor(cursor)
    if cursor_key is not None:
        items = [item for item in items if _sort_key(item) > cursor_key]
    page = items[:limit]
    has_more = len(items) > limit
    next_cursor = _encode_cursor(_sort_key(page[-1])) if has_more and page else None
    for item in page:
        item.pop("discriminator", None)
    return {
        "items": page,
        "count": len(page),
        "next_cursor": next_cursor,
    }
