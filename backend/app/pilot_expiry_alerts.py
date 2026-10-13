"""Telegram reminders before the AUTO pilot's policy or mandate lapses.

Both are deliberately time-boxed (policy up to 7 days, mandate 7 days). When
either expires the pilot silently stops acting AUTO; before 13.10.2026 the
owner only learned that from the readiness panel. Alerts fire only while the
policy actually grants AUTO, once per item and validity window (AuditLog
marker carries valid_until, so a renewal re-arms them). Telegram failure never
changes durable state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.integrations.telegram import notify_telegram, telegram_configured
from app.models.audit_log import AuditLog
from app.models.project import Project
from app.models.user import User

ALERT_AUDIT_ACTION = "pilot_expiry_alerted"
WARN_BEFORE = timedelta(hours=24)
STALE_AFTER = timedelta(days=2)
_MSK = timezone(timedelta(hours=3))
_LABELS = {
    "policy": ("политика AUTO-пилота", "истекла", "«Включить AUTO для задач и уведомлений на 7 дней»"),
    "authority": ("мандат AUTO-пилота", "истёк", "«Восстановить мандат пилота», затем снова включить AUTO"),
}


def _aware(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _message(kind: str, event: str, project: Project, valid_until: datetime) -> str:
    what, lapsed, how = _LABELS[kind]
    when = valid_until.astimezone(_MSK).strftime("%d.%m.%Y %H:%M МСК")
    head = f"PU Workspace, проект «{project.name}»: {what} "
    if event == "expired":
        return head + f"{lapsed} {when}. Автоматические действия остановлены. Продлите: Настройки → Готовность AUTO → {how}."
    return head + f"истекает {when}. Чтобы пилот не остановился, продлите: Настройки → Готовность AUTO → {how}."


def notify_pilot_expiry_once(*, sessions=SessionLocal, now: datetime | None = None) -> int:
    if not telegram_configured():
        return 0
    from app.autonomy_readiness import project_autonomy_readiness
    from app.pilot_product import load_product_pilot_settings

    settings = load_product_pilot_settings()
    if not settings.enabled:
        return 0
    now = now or datetime.now(timezone.utc)
    with sessions() as db:
        project = db.get(Project, settings.project_id)
        if project is None or project.archived_at is not None:
            return 0
        readiness = project_autonomy_readiness(db, project, now=now, actor=db.get(User, settings.owner_user_id))
    policy = readiness.get("policy") or {}
    if "AUTO" not in (policy.get("task_mode"), policy.get("notification_mode")):
        return 0  # nothing automatic to lose
    due = []
    for kind in ("policy", "authority"):
        valid_until = _aware((readiness.get(kind) or {}).get("valid_until"))
        if valid_until is None or valid_until > now + WARN_BEFORE or valid_until <= now - STALE_AFTER:
            continue
        due.append((kind, "expired" if valid_until <= now else "expiring", valid_until))
    sent = 0
    for kind, event, valid_until in due:
        marker = f"kind={kind};event={event};valid_until={valid_until.isoformat()}"
        with sessions.begin() as db:
            already = db.scalar(select(AuditLog.id).where(
                AuditLog.action == ALERT_AUDIT_ACTION,
                AuditLog.entity_type == "project",
                AuditLog.entity_id == project.id,
                AuditLog.details == marker,
            ).limit(1))
            if already is not None:
                continue
            if not notify_telegram(_message(kind, event, project, valid_until)):
                return sent
            db.add(AuditLog(action=ALERT_AUDIT_ACTION, entity_type="project",
                            entity_id=project.id, details=marker))
            sent += 1
    return sent
