"""Retention selection for off-site PostgreSQL backups.

Backup objects are named ``pu_workspace-YYYYMMDDTHHMMSSZ.dump.gpg`` (UTC).
Buckets are computed on the Europe/Moscow calendar date of the backup:

* daily   - the newest backup of each of the last ``daily`` distinct days;
* weekly  - the newest backup of each of the last ``weekly`` Sundays;
* monthly - the newest backup of each of the last ``monthly`` 1st-of-month days.

A backup kept by any rule is kept. Names that do not match the pattern are
never selected for deletion. Pure standard library: this module is copied into
the backend container and run there next to ``drive_ops.py``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

PREFIX = "pu_workspace-"
SUFFIX = ".dump.gpg"
NAME_RE = re.compile(r"^pu_workspace-(\d{8}T\d{6}Z)\.dump\.gpg$")
LOCAL_TZ = ZoneInfo("Europe/Moscow")


def backup_name(moment: datetime) -> str:
    return PREFIX + moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + SUFFIX


def parse_backup_name(name: str) -> datetime | None:
    match = NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _local_day(moment: datetime) -> date:
    return moment.astimezone(LOCAL_TZ).date()


def select_keep(names, daily: int = 7, weekly: int = 4, monthly: int = 6) -> set[str]:
    parsed = sorted(
        ((moment, name) for name in names if (moment := parse_backup_name(name)) is not None),
        reverse=True,
    )
    newest_per_day: dict[date, str] = {}
    for moment, name in parsed:
        newest_per_day.setdefault(_local_day(moment), name)
    days = sorted(newest_per_day, reverse=True)
    keep: set[str] = set()
    keep.update(newest_per_day[day] for day in days[:daily])
    sundays = [day for day in days if day.weekday() == 6]
    keep.update(newest_per_day[day] for day in sundays[:weekly])
    firsts = [day for day in days if day.day == 1]
    keep.update(newest_per_day[day] for day in firsts[:monthly])
    return keep


def select_delete(names, daily: int = 7, weekly: int = 4, monthly: int = 6) -> list[str]:
    keep = select_keep(names, daily, weekly, monthly)
    return sorted(name for name in names if parse_backup_name(name) is not None and name not in keep)
