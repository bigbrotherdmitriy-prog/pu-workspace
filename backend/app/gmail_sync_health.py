"""Telegram alert when a project's mail silently stops loading.

The background sweep job reports "completed" even when every message fails
(per-message errors are counted, not raised) or when a project's Google token
is dead. On 10-13.10.2026 project 17 loaded no mail for three days and nothing
said so. This reads the sweep's own audit trail and alerts the owner once per
project, problem kind and day. Telegram failure never changes durable work.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.integrations.telegram import notify_telegram, telegram_configured
from app.models.audit_log import AuditLog
from app.models.project import Project

ALERT_AUDIT_ACTION = "gmail_sync_health_alerted"
STREAK = 3
WINDOW = timedelta(minutes=45)
_COUNT = re.compile(r"\b(processed|failed)=(\d+)")
_ERRORS = re.compile(r"\berrors=([A-Za-z0-9_,]+)")


def _counts(details: str | None) -> dict[str, int]:
    return {key: int(value) for key, value in _COUNT.findall(details or "")}


def _problems(db, now: datetime) -> dict[int, tuple[str, str]]:
    """project_id -> (kind, human detail) for projects whose last STREAK sweep
    records in WINDOW are all failures."""
    rows = db.execute(
        select(AuditLog.entity_id, AuditLog.action, AuditLog.details)
        .where(AuditLog.action.in_(("gmail_sync", "gmail_auto_sync_failed")),
               AuditLog.entity_type == "project",
               AuditLog.created_at >= now - WINDOW)
        .order_by(AuditLog.entity_id, AuditLog.created_at.desc(), AuditLog.id.desc())
    ).all()
    latest: dict[int, list[tuple[str, str | None]]] = {}
    for project_id, action, details in rows:
        if project_id is not None and len(latest.setdefault(project_id, [])) < STREAK:
            latest[project_id].append((action, details))
    problems = {}
    for project_id, records in latest.items():
        if len(records) < STREAK:
            continue
        if all(action == "gmail_auto_sync_failed" for action, _ in records):
            problems[project_id] = ("connection", "не удаётся подключиться к почтовому ящику (доступ Google отозван или истёк)")
        elif all(action == "gmail_sync" and _counts(details).get("failed", 0) > 0
                 and _counts(details).get("processed", 0) == 0 for action, details in records):
            match = _ERRORS.search(records[0][1] or "")
            reason = f" (ошибка: {match.group(1)})" if match else ""
            problems[project_id] = ("ingest", f"все новые письма отклоняются при загрузке{reason}")
    return problems


def notify_gmail_sync_health_once(*, sessions=SessionLocal, now: datetime | None = None) -> int:
    if not telegram_configured():
        return 0
    now = now or datetime.now(timezone.utc)
    with sessions() as db:
        problems = _problems(db, now)
    sent = 0
    for project_id, (kind, text) in sorted(problems.items()):
        marker = f"kind={kind};date={now.date().isoformat()}"
        with sessions.begin() as db:
            project = db.get(Project, project_id)
            if project is None or project.archived_at is not None:
                continue
            already = db.scalar(select(AuditLog.id).where(
                AuditLog.action == ALERT_AUDIT_ACTION,
                AuditLog.entity_type == "project",
                AuditLog.entity_id == project_id,
                AuditLog.details == marker,
            ).limit(1))
            if already is not None:
                continue
            if not notify_telegram(
                f"PU Workspace: почта проекта «{project.name}» не загружается — {text}. "
                f"{STREAK} проверки подряд без единого загруженного письма. "
                "Если причина — доступ к ящику, продлите его в «Готовность AUTO»."
            ):
                return sent
            db.add(AuditLog(action=ALERT_AUDIT_ACTION, entity_type="project",
                            entity_id=project_id, details=marker))
            sent += 1
    return sent
