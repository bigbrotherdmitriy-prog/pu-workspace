"""Bulk backlog triage: preview, close/dismiss and undo stale tasks and drafts.

The owner clears hundreds of auto-extracted, never-touched tasks and response
drafts at once, but stays in control:

* nothing is deleted -- tasks move to the existing ``cancelled`` status
  (never ``completed``), drafts to the existing ``rejected`` status;
* every run can be previewed first (``dry_run``) with the exact count and
  sample titles;
* every real run is one batch whose AuditLog row stores each item's previous
  status, so ``undo`` restores it exactly. No migration: AuditLog suffices.

"Untouched" definitions (documented in the PR as well):

* task -- status is still the creation status ``assigned``, the external
  action was never approved (``external_action_status == "proposed"``), and
  there is no human change record: no ``task_history`` row, no
  ``task_due_date_history`` row and no ``management_history`` row for it.
  Rows written by this module's own bulk/undo actions do not count, so a
  closed-and-undone task is untouched again.
* draft -- status ``draft``, ``revision == 1`` (body/subject/recipient never
  edited), never approved, never sent or attempted, and no AuditLog row for
  it other than its creation (bulk rows excluded as above).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, not_, select
from sqlalchemy.orm import Session

from app.api.management import append_management_history
from app.models.audit_log import AuditLog
from app.models.management import ManagementHistory
from app.models.response_draft import ResponseDraft
from app.models.task import Task, TaskDueDateHistory, TaskHistory
from app.models.user import User

MAX_BULK_ITEMS = 1000
SAMPLE_SIZE = 20

TASK_OPEN_STATUSES = ("assigned", "in_progress")
TASK_BULK_TARGET_STATUS = "cancelled"
# Tasks with a live or finished external action are never bulk-closed: the
# local status would silently diverge from the published Google task.
TASK_EXTERNAL_LOCKED = ("approved", "queued", "executing", "executed", "unknown")
TASK_AUTO_SOURCES = ("document_analysis", "local_upload", "contract_analysis", "email", "telegram")
TASK_SOURCE_ALIASES = {"document": ("document_analysis", "local_upload", "contract_analysis")}
TASK_BULK_ACTIONS = ("bulk_closed", "bulk_close_undone")

DRAFT_BULK_SOURCE_STATUS = "draft"
DRAFT_BULK_TARGET_STATUS = "rejected"
DRAFT_CREATION_AUDIT = ("mail_draft_created", "project_contact_draft_created")
DRAFT_BULK_AUDIT = ("response_draft_bulk_dismissed", "response_draft_bulk_dismiss_undone")
# Drafts a person wrote or that a system flow created on purpose are not
# "auto-extracted" from documents/mail.
DRAFT_MANUAL_SOURCE_NAMES = ("mail-client", "corrective-follow-up")
DRAFT_MANUAL_SOURCE_PREFIXES = ("mail-draft:", "contact:", "provider-action:", "automation:")


class BulkFilter(BaseModel):
    older_than_days: int | None = Field(default=None, ge=0, le=3650)
    untouched_only: bool = False
    auto_extracted_only: bool = False
    source_type: str | None = Field(default=None, max_length=50)
    task_ids: list[int] | None = Field(default=None, max_length=MAX_BULK_ITEMS)
    draft_ids: list[int] | None = Field(default=None, max_length=MAX_BULK_ITEMS)


class BulkRequest(BaseModel):
    filter: BulkFilter = Field(default_factory=BulkFilter)
    dry_run: bool = True
    reason: str = Field(default="", max_length=2000)
    # Optional guard: the count the person saw in the preview. A mismatch
    # means the backlog changed in between and the run is refused.
    expected_count: int | None = Field(default=None, ge=0)


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _require_real_run(payload: BulkRequest) -> str:
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(422, "Укажите причину массового закрытия")
    return reason


def _check_size(count: int, expected: int | None):
    if count == 0:
        raise HTTPException(422, "Под фильтр не попало ни одной записи")
    if count > MAX_BULK_ITEMS:
        raise HTTPException(422, f"За один раз можно обработать не более {MAX_BULK_ITEMS} записей; сузьте фильтр")
    if expected is not None and expected != count:
        raise HTTPException(409, {"code": "bulk_preview_stale", "expected": expected, "actual": count})


def _batch_row(db: Session, action: str, entity_type: str, batch_id: int, project_id: int) -> tuple[AuditLog, dict]:
    row = db.scalar(select(AuditLog).where(
        AuditLog.id == batch_id, AuditLog.action == action, AuditLog.entity_type == entity_type,
    ))
    details = json.loads(row.details or "{}") if row is not None else {}
    if row is None or details.get("project_id") != project_id:
        raise HTTPException(404, "Пакет не найден")
    return row, details


def _ensure_not_undone(db: Session, undo_action: str, entity_type: str, batch_id: int):
    if db.scalar(select(AuditLog.id).where(
        AuditLog.action == undo_action, AuditLog.entity_type == entity_type, AuditLog.entity_id == batch_id,
    )) is not None:
        raise HTTPException(409, "Этот пакет уже отменён")


# --------------------------------------------------------------------- tasks

def task_bulk_query(project_id: int, flt: BulkFilter):
    query = select(Task).where(
        Task.project_id == project_id,
        Task.status.in_(TASK_OPEN_STATUSES),
        Task.external_action_status.not_in(TASK_EXTERNAL_LOCKED),
    )
    if flt.task_ids is not None:
        query = query.where(Task.id.in_(flt.task_ids or [-1]))
    if flt.older_than_days is not None:
        query = query.where(Task.created_at < _cutoff(flt.older_than_days))
    if flt.source_type:
        sources = TASK_SOURCE_ALIASES.get(flt.source_type, (flt.source_type,))
        query = query.where(Task.source_type.in_(sources))
    if flt.auto_extracted_only:
        query = query.where(Task.source_type.in_(TASK_AUTO_SOURCES), Task.needs_review.is_(True))
    if flt.untouched_only:
        query = query.where(
            Task.status == "assigned",
            Task.external_action_status == "proposed",
            not_(exists().where(TaskHistory.task_id == Task.id, TaskHistory.action.not_in(TASK_BULK_ACTIONS))),
            not_(exists().where(TaskDueDateHistory.task_id == Task.id)),
            not_(exists().where(
                ManagementHistory.entity_type == "task", ManagementHistory.entity_id == Task.id,
                ManagementHistory.action.not_in(TASK_BULK_ACTIONS),
            )),
        )
    return query


def _sample(rows, title) -> list[dict]:
    return [{"id": row.id, "title": title(row), "status": row.status, "created_at": row.created_at}
            for row in rows[:SAMPLE_SIZE]]


def bulk_close_tasks(db: Session, project_id: int, payload: BulkRequest, user: User) -> dict:
    query = task_bulk_query(project_id, payload.filter).order_by(Task.id.asc())
    if payload.dry_run:
        tasks = list(db.scalars(query).all())
        return {"dry_run": True, "count": len(tasks), "target_status": TASK_BULK_TARGET_STATUS,
                "sample": _sample(tasks, lambda t: t.title)}
    reason = _require_real_run(payload)
    tasks = list(db.scalars(query.with_for_update()).all())
    _check_size(len(tasks), payload.expected_count)
    batch = AuditLog(action="task_bulk_close", entity_type="task_bulk_batch", details="{}")
    db.add(batch)
    db.flush()
    items = []
    for task in tasks:
        old_status = task.status
        task.status = TASK_BULK_TARGET_STATUS
        task.completed_at = None
        task.record_version += 1
        items.append({"id": task.id, "status": old_status, "record_version": task.record_version})
        db.add(TaskHistory(
            task_id=task.id, action="bulk_closed", old_status=old_status, new_status=task.status,
            details=f"Массовое закрытие, пакет №{batch.id}: {reason}", changed_by_user_id=user.id,
        ))
        db.add(AuditLog(action="task_bulk_closed", entity_type="task", entity_id=task.id,
                        details=f"user={user.id}; batch={batch.id}; status={old_status}->{task.status}"))
        append_management_history(
            db, project_id=project_id, entity_type="task", entity_id=task.id,
            record_version=task.record_version, action="bulk_closed", actor_user_id=user.id,
            old_values={"status": old_status}, new_values={"status": task.status},
            evidence={"batch_id": batch.id}, reason=reason,
        )
    batch.details = json.dumps({
        "project_id": project_id, "actor_user_id": user.id, "reason": reason,
        "filter": payload.filter.model_dump(exclude_none=True), "target_status": TASK_BULK_TARGET_STATUS,
        "items": items,
    }, ensure_ascii=False)
    db.commit()
    return {"dry_run": False, "batch_id": batch.id, "count": len(items),
            "target_status": TASK_BULK_TARGET_STATUS, "task_ids": [item["id"] for item in items]}


def undo_bulk_close_tasks(db: Session, project_id: int, batch_id: int, user: User) -> dict:
    _, details = _batch_row(db, "task_bulk_close", "task_bulk_batch", batch_id, project_id)
    _ensure_not_undone(db, "task_bulk_close_undo", "task_bulk_batch", batch_id)
    restored, skipped = [], []
    for item in details.get("items", []):
        task = db.scalar(select(Task).where(Task.id == item["id"], Task.project_id == project_id).with_for_update())
        # Restore only what this batch left untouched since; a task someone
        # reopened or edited afterwards keeps the newer human decision.
        if task is None or task.status != TASK_BULK_TARGET_STATUS or task.record_version != item["record_version"]:
            skipped.append(item["id"])
            continue
        task.status = item["status"]
        task.record_version += 1
        restored.append(task.id)
        db.add(TaskHistory(
            task_id=task.id, action="bulk_close_undone", old_status=TASK_BULK_TARGET_STATUS, new_status=task.status,
            details=f"Отмена массового закрытия, пакет №{batch_id}", changed_by_user_id=user.id,
        ))
        db.add(AuditLog(action="task_bulk_close_undone", entity_type="task", entity_id=task.id,
                        details=f"user={user.id}; batch={batch_id}; status={TASK_BULK_TARGET_STATUS}->{task.status}"))
        append_management_history(
            db, project_id=project_id, entity_type="task", entity_id=task.id,
            record_version=task.record_version, action="bulk_close_undone", actor_user_id=user.id,
            old_values={"status": TASK_BULK_TARGET_STATUS}, new_values={"status": task.status},
            evidence={"batch_id": batch_id}, reason="Отмена массового закрытия",
        )
    db.add(AuditLog(action="task_bulk_close_undo", entity_type="task_bulk_batch", entity_id=batch_id,
                    details=json.dumps({"project_id": project_id, "actor_user_id": user.id,
                                        "restored": restored, "skipped": skipped})))
    db.commit()
    return {"batch_id": batch_id, "restored": len(restored), "skipped": skipped}


# -------------------------------------------------------------------- drafts

def draft_bulk_query(project_id: int, flt: BulkFilter):
    # Only plain, never-approved, never-dispatched drafts are eligible. Any
    # approved/queued/sending/sent/unknown/failed draft is out by construction.
    query = select(ResponseDraft).where(
        ResponseDraft.project_id == project_id,
        ResponseDraft.status == DRAFT_BULK_SOURCE_STATUS,
        ResponseDraft.approved_revision.is_(None),
        ResponseDraft.sent_at.is_(None),
        ResponseDraft.sent_external_id.is_(None),
        ResponseDraft.send_attempts == 0,
        ResponseDraft.source_file_name != "corrective-follow-up",
    )
    if flt.draft_ids is not None:
        query = query.where(ResponseDraft.id.in_(flt.draft_ids or [-1]))
    if flt.older_than_days is not None:
        query = query.where(ResponseDraft.created_at < _cutoff(flt.older_than_days))
    if flt.source_type == "email":
        query = query.where(ResponseDraft.message_id.is_not(None))
    elif flt.source_type == "document":
        query = query.where(ResponseDraft.message_id.is_(None))
    elif flt.source_type:
        raise HTTPException(422, "source_type для черновиков: email или document")
    if flt.auto_extracted_only:
        query = query.where(
            ResponseDraft.source_file_name.not_in(DRAFT_MANUAL_SOURCE_NAMES),
            and_(*(not_(ResponseDraft.source_file_id.startswith(prefix)) for prefix in DRAFT_MANUAL_SOURCE_PREFIXES)),
        )
    if flt.untouched_only:
        query = query.where(
            ResponseDraft.revision == 1,
            not_(exists().where(
                AuditLog.entity_type == "response_draft", AuditLog.entity_id == ResponseDraft.id,
                AuditLog.action.not_in(DRAFT_CREATION_AUDIT + DRAFT_BULK_AUDIT),
            )),
        )
    return query


def bulk_dismiss_drafts(db: Session, project_id: int, payload: BulkRequest, user: User) -> dict:
    query = draft_bulk_query(project_id, payload.filter).order_by(ResponseDraft.id.asc())
    if payload.dry_run:
        drafts = list(db.scalars(query).all())
        return {"dry_run": True, "count": len(drafts), "target_status": DRAFT_BULK_TARGET_STATUS,
                "sample": _sample(drafts, lambda d: d.subject)}
    reason = _require_real_run(payload)
    drafts = list(db.scalars(query.with_for_update()).all())
    _check_size(len(drafts), payload.expected_count)
    batch = AuditLog(action="response_draft_bulk_dismiss", entity_type="response_draft_bulk_batch", details="{}")
    db.add(batch)
    db.flush()
    items = []
    for draft in drafts:
        old_status = draft.status
        draft.status = DRAFT_BULK_TARGET_STATUS
        items.append({"id": draft.id, "status": old_status, "revision": draft.revision})
        db.add(AuditLog(action="response_draft_bulk_dismissed", entity_type="response_draft", entity_id=draft.id,
                        details=f"user={user.id}; batch={batch.id}; status={old_status}->{draft.status}; reason={reason}"))
    batch.details = json.dumps({
        "project_id": project_id, "actor_user_id": user.id, "reason": reason,
        "filter": payload.filter.model_dump(exclude_none=True), "target_status": DRAFT_BULK_TARGET_STATUS,
        "items": items,
    }, ensure_ascii=False)
    db.commit()
    return {"dry_run": False, "batch_id": batch.id, "count": len(items),
            "target_status": DRAFT_BULK_TARGET_STATUS, "draft_ids": [item["id"] for item in items]}


def undo_bulk_dismiss_drafts(db: Session, project_id: int, batch_id: int, user: User) -> dict:
    _, details = _batch_row(db, "response_draft_bulk_dismiss", "response_draft_bulk_batch", batch_id, project_id)
    _ensure_not_undone(db, "response_draft_bulk_dismiss_undo", "response_draft_bulk_batch", batch_id)
    restored, skipped = [], []
    for item in details.get("items", []):
        draft = db.scalar(select(ResponseDraft).where(
            ResponseDraft.id == item["id"], ResponseDraft.project_id == project_id,
        ).with_for_update())
        if draft is None or draft.status != DRAFT_BULK_TARGET_STATUS or draft.revision != item["revision"]:
            skipped.append(item["id"])
            continue
        draft.status = item["status"]
        restored.append(draft.id)
        db.add(AuditLog(action="response_draft_bulk_dismiss_undone", entity_type="response_draft", entity_id=draft.id,
                        details=f"user={user.id}; batch={batch_id}; status={DRAFT_BULK_TARGET_STATUS}->{draft.status}"))
    db.add(AuditLog(action="response_draft_bulk_dismiss_undo", entity_type="response_draft_bulk_batch", entity_id=batch_id,
                    details=json.dumps({"project_id": project_id, "actor_user_id": user.id,
                                        "restored": restored, "skipped": skipped})))
    db.commit()
    return {"batch_id": batch_id, "restored": len(restored), "skipped": skipped}
