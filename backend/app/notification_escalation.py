from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ALLOWED_CHANNELS = frozenset({"in_app", "email", "telegram"})


def require_iana_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("unknown IANA timezone") from error


def _resolved_local(day: date, wall_time: time, zone: ZoneInfo) -> datetime:
    """Resolve DST gaps forward and choose the later instant for ambiguous time."""
    naive = datetime.combine(day, wall_time)
    early = naive.replace(tzinfo=zone, fold=0)
    late = naive.replace(tzinfo=zone, fold=1)
    early_roundtrip = early.astimezone(timezone.utc).astimezone(zone)
    late_roundtrip = late.astimezone(timezone.utc).astimezone(zone)
    early_valid = early_roundtrip.replace(tzinfo=None) == naive
    late_valid = late_roundtrip.replace(tzinfo=None) == naive
    if early_valid and late_valid and early.utcoffset() != late.utcoffset():
        return late
    if late_valid:
        return late
    if early_valid:
        return early
    # A nonexistent wall time (spring gap) is shifted forward by the gap.
    return early_roundtrip


def deadline_utc(day: date, wall_time: time, timezone_name: str) -> datetime:
    return _resolved_local(day, wall_time, require_iana_timezone(timezone_name)).astimezone(timezone.utc)


def outside_quiet_hours(instant: datetime, *, timezone_name: str,
                        quiet_start: time, quiet_end: time) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    if quiet_start == quiet_end:
        return instant.astimezone(timezone.utc)
    zone = require_iana_timezone(timezone_name)
    local = instant.astimezone(zone)
    current = local.timetz().replace(tzinfo=None)
    if quiet_start < quiet_end:
        in_quiet = quiet_start <= current < quiet_end
        target_day = local.date()
    else:
        in_quiet = current >= quiet_start or current < quiet_end
        target_day = local.date() + timedelta(days=1) if current >= quiet_start else local.date()
    if not in_quiet:
        return instant.astimezone(timezone.utc)
    return _resolved_local(target_day, quiet_end, zone).astimezone(timezone.utc)


def run_escalation_proposal(payload: dict) -> dict:
    """Worker handler deliberately emits a proposal and performs no external action."""
    required = ("organization_id", "project_id", "user_id", "notification_id", "obligation_id", "step")
    if any(type(payload.get(key)) is not int for key in required):
        raise ValueError("invalid escalation proposal identity")
    channels = payload.get("channels")
    if not isinstance(channels, list) or not channels or any(value not in ALLOWED_CHANNELS for value in channels):
        raise ValueError("invalid escalation proposal channels")
    return {
        "status": "proposed",
        "proposal": "notification_escalation_dispatch",
        "organization_id": payload["organization_id"],
        "project_id": payload["project_id"],
        "user_id": payload["user_id"],
        "notification_id": payload["notification_id"],
        "obligation_id": payload["obligation_id"],
        "step": payload["step"],
        "channels": list(channels),
        "external_action_performed": False,
    }
