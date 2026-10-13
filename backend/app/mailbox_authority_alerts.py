"""Telegram reminders before a mailbox authority grant lapses.

An expired MailboxAuthorityState makes every Gmail ingest fail closed, and
before this module nothing told the owner (10.10.2026: project 17 stopped
loading mail for three days). Each grant window gets at most one "expiring"
and one "expired" alert; the AuditLog marker carries the grant's valid_until,
so a renewal opens a fresh window and re-arms both alerts. Telegram failure
never changes the grant itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.integrations.telegram import notify_telegram, telegram_configured
from app.models.audit_log import AuditLog
from app.models.mailbox_identity import MailboxAuthorityState

ALERT_AUDIT_ACTION = "mailbox_authority_expiry_alerted"
WARN_BEFORE = timedelta(hours=24)
STALE_AFTER = timedelta(days=2)
_MSK = timezone(timedelta(hours=3))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _message(event: str, valid_until: datetime) -> str:
    when = valid_until.astimezone(_MSK).strftime("%d.%m.%Y %H:%M МСК")
    where = "Настройки → Готовность AUTO → «Продлить доступ к почте на 7 дней»"
    if event == "expired":
        return (f"PU Workspace: доступ системы к почтовому ящику истёк {when}. "
                f"Новые письма не загружаются, пока доступ не продлён: {where}.")
    return (f"PU Workspace: доступ системы к почтовому ящику истекает {when}. "
            f"Чтобы почта не остановилась, продлите его: {where}.")


def notify_mailbox_authority_expiry_once(*, sessions=SessionLocal, now: datetime | None = None,
                                         limit: int = 20) -> int:
    """One transaction per grant, so a Telegram exception never rolls back the
    markers of alerts already delivered in the same pass. Grants that lapsed
    more than STALE_AFTER ago are no longer scanned: their "expired" alert went
    out within minutes, and they must not crowd fresh grants out of `limit`."""
    if not telegram_configured():
        return 0
    now = now or datetime.now(timezone.utc)
    with sessions() as db:
        candidate_ids = list(db.scalars(
            select(MailboxAuthorityState.id)
            .where(
                MailboxAuthorityState.state == "active",
                MailboxAuthorityState.principal_kind == "user",
                MailboxAuthorityState.valid_until <= now + WARN_BEFORE,
                MailboxAuthorityState.valid_until > now - STALE_AFTER,
            )
            .order_by(MailboxAuthorityState.valid_until)
            .limit(max(0, min(int(limit), 100)))
        ))
    sent = 0
    for grant_id in candidate_ids:
        with sessions.begin() as db:
            grant = db.scalar(select(MailboxAuthorityState).where(
                MailboxAuthorityState.id == grant_id,
                MailboxAuthorityState.state == "active",
            ).with_for_update(skip_locked=True))
            if grant is None:
                continue
            valid_until = _aware(grant.valid_until)
            if valid_until > now + WARN_BEFORE:
                continue  # renewed meanwhile
            event = "expired" if valid_until <= now else "expiring"
            marker = f"event={event};valid_until={valid_until.isoformat()}"
            already = db.scalar(select(AuditLog.id).where(
                AuditLog.action == ALERT_AUDIT_ACTION,
                AuditLog.entity_type == "mailbox_authority",
                AuditLog.entity_id == grant.id,
                AuditLog.details == marker,
            ).limit(1))
            if already is not None:
                continue
            if not notify_telegram(_message(event, valid_until)):
                return sent
            db.add(AuditLog(action=ALERT_AUDIT_ACTION, entity_type="mailbox_authority",
                            entity_id=grant.id, details=marker))
            sent += 1
    return sent
