from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.governance import Decision, Risk
from app.models.management import Obligation
from app.models.task import Task


def _deadline_at(row: Obligation) -> datetime | None:
    if row.due_date is None:
        return None
    return datetime.combine(row.due_date, row.due_time or time(23, 59), ZoneInfo(row.timezone or "Europe/Moscow"))


def attention_page(db: Session, *, project_id: int, now: datetime | None = None, kinds: set[str] | None = None,
                   offset: int = 0, limit: int = 50) -> dict:
    current = now or datetime.now(timezone.utc)
    items: list[dict] = []
    for row in db.scalars(select(Obligation).where(Obligation.project_id == project_id)).all():
        if row.status not in {"needs_confirmation", "confirmed", "in_progress"}:
            continue
        deadline = _deadline_at(row)
        overdue = bool(deadline and deadline.astimezone(timezone.utc) < current.astimezone(timezone.utc))
        kind = "overdue_obligation" if overdue else "obligation_review" if row.status == "needs_confirmation" else "obligation"
        items.append({"kind": kind, "entity_type": "obligation", "entity_id": row.id,
                      "record_version": row.record_version, "title": row.title,
                      "priority": "critical" if overdue else "high" if row.review_state == "needs_review" else "normal",
                      "due_at": deadline.isoformat() if deadline else None, "status": row.status,
                      "explanation": "deadline_passed" if overdue else "human_review_required" if row.review_state == "needs_review" else "open",
                      "evidence_pins": row.evidence_pins or []})
    for row in db.scalars(select(Task).where(Task.project_id == project_id,
                                             Task.status.in_(["assigned", "in_progress"]))).all():
        overdue = bool(row.due_date and row.due_date < current.date())
        items.append({"kind": "overdue_task" if overdue else "task", "entity_type": "task", "entity_id": row.id,
                      "record_version": row.record_version, "title": row.title,
                      "priority": "critical" if overdue else row.priority, "due_at": row.due_date.isoformat() if row.due_date else None,
                      "status": row.status, "explanation": "deadline_passed" if overdue else "open",
                      "evidence_pins": []})
    for entity_type, model in (("risk", Risk), ("decision", Decision)):
        open_states = ["needs_confirmation", "confirmed", "mitigating"] if model is Risk else ["needs_confirmation", "confirmed", "decided"]
        for row in db.scalars(select(model).where(model.project_id == project_id, model.status.in_(open_states))).all():
            severity = row.criticality if model is Risk else "high"
            items.append({"kind": entity_type, "entity_type": entity_type, "entity_id": row.id,
                          "record_version": row.record_version,
                          "title": row.title if model is Risk else row.question,
                          "priority": "high" if severity in {"high", "critical"} else "normal",
                          "due_at": None, "status": row.status,
                          "explanation": "human_review_required" if row.review_state == "needs_review" else "open",
                          "evidence_pins": row.evidence_pins or []})
    if kinds:
        items = [item for item in items if item["kind"] in kinds or item["entity_type"] in kinds]
    rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    items.sort(key=lambda item: (rank.get(item["priority"], 2), item["due_at"] or "9999", item["entity_type"], item["entity_id"]))
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit,
            "generated_at": current.isoformat(), "external_actions_created": False}
