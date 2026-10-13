"""Durable push outbox for MVP-3 notifications (V6-04).

Every new ``Notification`` row for a user with a registered device queues one
``notifications.push.deliver`` job per device in the same transaction, so a
notification that is committed is also committed for push delivery.  The job
lives on the existing ``background_jobs`` queue: an FCM outage is retried with
the queue's backoff instead of losing the message, and quiet hours from the
user's ``NotificationPolicy`` delay delivery to their local morning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import event, insert, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.integrations.push import (
    FcmPushAdapter, PushTokenRejected, fcm_configured, push_message_for,
)
from app.models.job import BackgroundJob
from app.models.management import Notification, NotificationPolicy
from app.models.notification_device import UserDeviceToken
from app.notification_escalation import outside_quiet_hours

PUSH_JOB_KIND = "notifications.push.deliver"
# With the queue's 5 s .. 300 s backoff this keeps retrying for about four
# hours before the job is dead-lettered (and can still be redriven by admin).
PUSH_MAX_ATTEMPTS = 50
PUSH_PRIORITY = 90
# V6-03 plan: at most one push per entity per device in this window.
ENTITY_PUSH_WINDOW_SECONDS = 60

_session_factory: Callable[[], Session] = SessionLocal
_adapter_factory: Callable[[], FcmPushAdapter] = FcmPushAdapter
_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def install_push_runtime(session_factory: Callable[[], Session] | None = None, *,
                         adapter_factory: Callable[[], FcmPushAdapter] | None = None,
                         clock: Callable[[], datetime] | None = None) -> None:
    global _session_factory, _adapter_factory, _clock
    _session_factory = session_factory or SessionLocal
    _adapter_factory = adapter_factory or FcmPushAdapter
    _clock = clock or (lambda: datetime.now(timezone.utc))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _deliverable_at(policy, now: datetime) -> datetime | None:
    """None when the user switched notifications off for this project."""
    if policy is None:
        return now
    if not policy.enabled:
        return None
    return outside_quiet_hours(now, timezone_name=policy.timezone,
                               quiet_start=policy.quiet_start, quiet_end=policy.quiet_end)


def _job_key(notification_id: int, device_id: int, suffix: str = "") -> str:
    return f"push:{notification_id}:{device_id}{suffix}"


def _entity_key_prefix(device_id: int, target: Notification) -> str:
    return f"push-entity:{device_id}:{target.entity_type}:{target.entity_id}:"


def _recently_pushed(connection, device_id: int, target: Notification, now_epoch: int) -> bool:
    """True when this entity already queued a push to this device within the window.

    The queue time is part of the key so the check uses the same clock as the
    hook, not the database's created_at.
    """
    prefix = _entity_key_prefix(device_id, target)
    keys = connection.execute(select(BackgroundJob.idempotency_key).where(
        BackgroundJob.kind == PUSH_JOB_KIND,
        BackgroundJob.idempotency_key.startswith(prefix, autoescape=True),
    )).scalars()
    for key in keys:
        queued_at = key[len(prefix):].split(":", 1)[0]
        if queued_at.isdigit() and now_epoch - int(queued_at) < ENTITY_PUSH_WINDOW_SECONDS:
            return True
    return False


@event.listens_for(Notification, "after_insert")
def _queue_push_for_new_notification(_mapper, connection, target: Notification) -> None:
    devices = list(connection.execute(
        select(UserDeviceToken.id).where(UserDeviceToken.user_id == target.user_id)
        .order_by(UserDeviceToken.id)
    ).scalars())
    if not devices or not fcm_configured():
        return
    policy = connection.execute(select(
        NotificationPolicy.enabled, NotificationPolicy.timezone,
        NotificationPolicy.quiet_start, NotificationPolicy.quiet_end,
    ).where(NotificationPolicy.project_id == target.project_id,
            NotificationPolicy.user_id == target.user_id)).first()
    now = _aware(_clock())
    available_at = _deliverable_at(policy, now)
    if available_at is None:
        return
    now_epoch = int(now.timestamp())
    rows = [{
        "kind": PUSH_JOB_KIND,
        "payload": {"notification_id": target.id, "device_id": device_id},
        "status": "queued", "priority": PUSH_PRIORITY, "attempts": 0,
        "max_attempts": PUSH_MAX_ATTEMPTS, "progress": 0,
        "available_at": available_at,
        "idempotency_key": f"{_entity_key_prefix(device_id, target)}{now_epoch}:{target.id}",
    } for device_id in devices if not _recently_pushed(connection, device_id, target, now_epoch)]
    if rows:
        connection.execute(insert(BackgroundJob), rows)


def _identity(payload: dict) -> tuple[int, int]:
    notification_id, device_id = payload.get("notification_id"), payload.get("device_id")
    if type(notification_id) is not int or type(device_id) is not int:
        raise ValueError("invalid push delivery identity")
    return notification_id, device_id


def run_push_delivery(payload: dict) -> dict:
    notification_id, device_id = _identity(payload)
    db = _session_factory()
    owned = _session_factory is SessionLocal
    try:
        return _deliver(db, notification_id, device_id, _aware(_clock()))
    except Exception:
        db.rollback()
        raise
    finally:
        if owned:
            db.close()


def _deliver(db: Session, notification_id: int, device_id: int, now: datetime) -> dict:
    notification = db.get(Notification, notification_id)
    if notification is None:
        return {"status": "skipped", "reason": "notification_missing"}
    device = db.get(UserDeviceToken, device_id)
    # The phone may have been signed in by someone else since the job was queued.
    if device is None or device.user_id != notification.user_id:
        return {"status": "skipped", "reason": "device_gone"}
    if notification.is_read:
        return {"status": "skipped", "reason": "already_read"}
    policy = db.scalar(select(NotificationPolicy).where(
        NotificationPolicy.project_id == notification.project_id,
        NotificationPolicy.user_id == notification.user_id,
    ))
    available_at = _deliverable_at(policy, now)
    if available_at is None:
        return {"status": "skipped", "reason": "notifications_disabled"}
    if available_at > now:
        # Retries after an outage must not ring the phone at night either.
        # Imported here: app.jobs.queue imports app.models, which loads this module.
        from app.jobs.queue import enqueue
        enqueue(db, PUSH_JOB_KIND, {"notification_id": notification_id, "device_id": device_id},
                priority=PUSH_PRIORITY, max_attempts=PUSH_MAX_ATTEMPTS, available_at=available_at,
                idempotency_key=_job_key(notification_id, device_id, f":at:{available_at.isoformat()}"))
        return {"status": "deferred", "available_at": available_at.isoformat()}
    message = push_message_for(kind=notification.kind, project_id=notification.project_id,
                               entity_type=notification.entity_type, entity_id=notification.entity_id)
    try:
        _adapter_factory().send(device.fcm_token, message)
    except PushTokenRejected as rejected:
        db.delete(device)
        db.commit()
        return {"status": "token_removed", "reason": str(rejected)}
    return {"status": "sent"}
