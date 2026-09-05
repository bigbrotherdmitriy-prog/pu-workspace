from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.v54_refs import VersionPin
from app.models.governance import Decision, Risk
from app.models.management import Obligation
from app.models.project import Project
from app.models.task import Task
from app.models.v54_pilot import Evidence, SourceReference


MAX_ATTENTION_SCAN_ROWS_PER_TYPE = 1000


def _deadline_at(row: Obligation) -> datetime | None:
    if row.due_date is None:
        return None
    return datetime.combine(row.due_date, row.due_time or time(23, 59), ZoneInfo(row.timezone or "Europe/Moscow"))


def attention_page(db: Session, *, project_id: int, now: datetime | None = None, kinds: set[str] | None = None,
                   offset: int = 0, limit: int = 50) -> dict:
    current = now or datetime.now(timezone.utc)
    items: list[dict] = []
    scan_truncated = False
    rows = db.scalars(select(Obligation).where(Obligation.project_id == project_id)
                      .order_by(Obligation.id).limit(MAX_ATTENTION_SCAN_ROWS_PER_TYPE + 1)).all()
    scan_truncated = scan_truncated or len(rows) > MAX_ATTENTION_SCAN_ROWS_PER_TYPE
    for row in rows[:MAX_ATTENTION_SCAN_ROWS_PER_TYPE]:
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
    rows = db.scalars(select(Task).where(Task.project_id == project_id,
                                        Task.status.in_(["assigned", "in_progress"]))
                      .order_by(Task.id).limit(MAX_ATTENTION_SCAN_ROWS_PER_TYPE + 1)).all()
    scan_truncated = scan_truncated or len(rows) > MAX_ATTENTION_SCAN_ROWS_PER_TYPE
    for row in rows[:MAX_ATTENTION_SCAN_ROWS_PER_TYPE]:
        overdue = bool(row.due_date and row.due_date < current.date())
        items.append({"kind": "overdue_task" if overdue else "task", "entity_type": "task", "entity_id": row.id,
                      "record_version": row.record_version, "title": row.title,
                      "priority": "critical" if overdue else row.priority, "due_at": row.due_date.isoformat() if row.due_date else None,
                      "status": row.status, "explanation": "deadline_passed" if overdue else "open",
                      "evidence_pins": []})
    for entity_type, model in (("risk", Risk), ("decision", Decision)):
        open_states = ["needs_confirmation", "confirmed", "mitigating"] if model is Risk else ["needs_confirmation", "confirmed", "decided"]
        rows = db.scalars(select(model).where(model.project_id == project_id, model.status.in_(open_states))
                          .order_by(model.id).limit(MAX_ATTENTION_SCAN_ROWS_PER_TYPE + 1)).all()
        scan_truncated = scan_truncated or len(rows) > MAX_ATTENTION_SCAN_ROWS_PER_TYPE
        for row in rows[:MAX_ATTENTION_SCAN_ROWS_PER_TYPE]:
            severity = row.criticality if model is Risk else "high"
            items.append({"kind": entity_type, "entity_type": entity_type, "entity_id": row.id,
                          "record_version": row.record_version,
                          "title": row.title if model is Risk else row.question,
                          "priority": "high" if severity in {"high", "critical"} else "normal",
                          "due_at": None, "status": row.status,
                          "explanation": "human_review_required" if row.review_state == "needs_review" else "open",
                          "evidence_pins": row.evidence_pins or []})
    project = db.get(Project, project_id)
    parsed: dict[str, VersionPin] = {}
    if project is not None:
        for item in items:
            for candidate in item["evidence_pins"]:
                try:
                    pin = VersionPin.model_validate(candidate)
                except (TypeError, ValueError):
                    continue
                if (pin.ref.type == "evidence" and pin.version_kind == "revision" and pin.value == 1
                        and pin.ref.tenant_id.value == str(project.organization_id)):
                    parsed[pin.ref.id.value] = pin
    allowed = set()
    if parsed and project is not None:
        allowed = set(db.scalars(select(Evidence.id).join(SourceReference, and_(
            SourceReference.id == Evidence.source_id,
            SourceReference.organization_id == Evidence.organization_id,
        )).where(
            Evidence.organization_id == project.organization_id,
            Evidence.id.in_(parsed),
            SourceReference.origin_project_id == project_id,
        )))
    for item in items:
        safe = []
        for candidate in item["evidence_pins"]:
            try:
                pin = VersionPin.model_validate(candidate)
            except (TypeError, ValueError):
                continue
            if pin.ref.id.value in allowed:
                safe.append(pin.model_dump(mode="json"))
        item["evidence_pins"] = safe
    if kinds:
        items = [item for item in items if item["kind"] in kinds or item["entity_type"] in kinds]
    rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    items.sort(key=lambda item: (rank.get(item["priority"], 2), item["due_at"] or "9999", item["entity_type"], item["entity_id"]))
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit,
            "generated_at": current.isoformat(), "external_actions_created": False,
            "scan_truncated": scan_truncated, "scan_cap_per_type": MAX_ATTENTION_SCAN_ROWS_PER_TYPE}
