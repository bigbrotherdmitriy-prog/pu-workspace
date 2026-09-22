"""Best-effort, content-free incident alerts for the product AUTO pilot.

The durable job ledger remains authoritative.  This module only projects new
terminal product-pilot failures to the already configured operator Telegram
chat.  Payloads, evidence, tenant data and error text are deliberately omitted.
"""
from __future__ import annotations

from sqlalchemy import exists, select

from app.database import SessionLocal
from app.integrations.telegram import notify_telegram, telegram_configured
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.pilot_dispatch import PRODUCT_KIND


ALERT_AUDIT_ACTION = "product_auto_incident_alerted"
TERMINAL_INCIDENT_STATUSES = ("failed", "dead_letter")


def _message(job: BackgroundJob) -> str:
    return (
        "PU Workspace: AUTO incident. "
        f"job_id={job.id}; kind={job.kind}; status={job.status}. "
        "Inspect the durable receipt before any retry; operator review is required."
    )


def notify_product_auto_incidents_once(*, sessions=SessionLocal, limit: int = 20) -> int:
    """Alert on unreported terminal AUTO jobs and persist a content-free marker.

    The job row is locked while the alert is sent so concurrent schedulers do
    not normally duplicate it.  A process crash after Telegram accepts the
    message but before the transaction commits can still repeat an alert; that
    is preferable to silently losing an incident.  Domain processing never
    depends on the notification channel.
    """
    if not telegram_configured():
        return 0
    sent = 0
    for _ in range(max(0, min(int(limit), 100))):
        with sessions.begin() as db:
            already_alerted = exists(select(AuditLog.id).where(
                AuditLog.action == ALERT_AUDIT_ACTION,
                AuditLog.entity_type == "background_job",
                AuditLog.entity_id == BackgroundJob.id,
            ))
            job = db.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == PRODUCT_KIND,
                    BackgroundJob.status.in_(TERMINAL_INCIDENT_STATUSES),
                    ~already_alerted,
                )
                .order_by(BackgroundJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return sent
            if not notify_telegram(_message(job)):
                return sent
            db.add(AuditLog(
                action=ALERT_AUDIT_ACTION,
                entity_type="background_job",
                entity_id=job.id,
                details=f"kind={job.kind};status={job.status};channel=telegram",
            ))
            sent += 1
    return sent
