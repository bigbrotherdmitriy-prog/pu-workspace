import importlib.util
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "scripts" / "backup"


def load(name):
    spec = importlib.util.spec_from_file_location(f"backup_{name}", BACKUP / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rotation = load("rotation")


def nightly(day: date) -> str:
    # 03:30 Moscow == 00:30 UTC
    return rotation.backup_name(datetime(day.year, day.month, day.day, 0, 30, tzinfo=timezone.utc))


def series(last: date, days: int) -> list[str]:
    return [nightly(last - timedelta(days=offset)) for offset in range(days)]


def test_name_round_trip_and_foreign_names_ignored():
    moment = datetime(2026, 10, 14, 0, 30, 5, tzinfo=timezone.utc)
    name = rotation.backup_name(moment)
    assert name == "pu_workspace-20261014T003005Z.dump.gpg"
    assert rotation.parse_backup_name(name) == moment
    for foreign in ["notes.txt", "pu_workspace-2026.dump.gpg", "pu_workspace-20261399T000000Z.dump.gpg",
                    "pu_workspace-20261014T003005Z.dump"]:
        assert rotation.parse_backup_name(foreign) is None
    assert rotation.select_delete(["notes.txt", name]) == []


def test_fewer_backups_than_retention_keeps_everything():
    names = series(date(2026, 10, 14), 5)
    assert rotation.select_keep(names) == set(names)
    assert rotation.select_delete(names) == []


def test_one_year_of_nightly_backups_keeps_7_daily_4_sundays_6_firsts():
    last = date(2026, 10, 14)  # Wednesday
    names = series(last, 365)
    keep = rotation.select_keep(names)
    kept_days = sorted({rotation.parse_backup_name(n).date() for n in keep}, reverse=True)

    daily = [last - timedelta(days=i) for i in range(7)]
    sundays = [date(2026, 10, 11), date(2026, 10, 4), date(2026, 9, 27), date(2026, 9, 20)]
    firsts = [date(2026, 10, 1), date(2026, 9, 1), date(2026, 8, 1), date(2026, 7, 1),
              date(2026, 6, 1), date(2026, 5, 1)]
    assert set(kept_days) == set(daily) | set(sundays) | set(firsts)
    # 7 daily + 3 Sundays outside the daily window + 6 firsts (none in the daily window)
    assert len(keep) == 7 + 3 + 6
    assert set(rotation.select_delete(names)) == set(names) - keep


def test_newest_backup_of_a_day_wins_and_older_same_day_is_deleted():
    day = date(2026, 10, 14)
    early = rotation.backup_name(datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc))
    late = rotation.backup_name(datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc))
    names = [early, late] + series(day - timedelta(days=1), 3)
    keep = rotation.select_keep(names)
    assert late in keep and early not in keep
    assert rotation.select_delete(names) == [early]


def test_days_are_bucketed_on_moscow_calendar():
    # 22:30 UTC on Saturday is already Sunday 01:30 in Moscow -> weekly slot.
    sunday_msk = rotation.backup_name(datetime(2026, 10, 10, 22, 30, tzinfo=timezone.utc))
    # 21:30 UTC on 30 Sep is 1 Oct 00:30 in Moscow -> monthly slot.
    first_msk = rotation.backup_name(datetime(2026, 9, 30, 21, 30, tzinfo=timezone.utc))
    recent = series(date(2026, 11, 30), 7)
    keep = rotation.select_keep(recent + [sunday_msk, first_msk])
    assert sunday_msk in keep
    assert first_msk in keep


def test_gaps_in_history_still_keep_last_n_existing_days():
    names = [nightly(date(2026, 10, 14) - timedelta(days=2 * i)) for i in range(20)]
    keep = rotation.select_keep(names, daily=7, weekly=0, monthly=0)
    assert keep == set(sorted(names, reverse=True)[:7])


def test_custom_retention_counts():
    names = series(date(2026, 10, 14), 60)
    assert len(rotation.select_keep(names, daily=1, weekly=0, monthly=0)) == 1
    assert len(rotation.select_keep(names, daily=0, weekly=2, monthly=1)) == 3


@pytest.mark.parametrize("name", ["offsite_backup.sh", "restore_check.sh", "common.sh"])
def test_backup_shell_scripts_parse(name):
    path = BACKUP / name
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_backup_scripts_are_strict_and_never_inline_secrets():
    for name in ["offsite_backup.sh", "restore_check.sh"]:
        text = (BACKUP / name).read_text(encoding="utf-8")
        assert "set -eu" in text and "set -o pipefail" in text
        assert "trap finish EXIT" in text
        assert "notify_failure" in text
        assert "--passphrase " not in text  # only --passphrase-file
    offsite = (BACKUP / "offsite_backup.sh").read_text(encoding="utf-8")
    assert "--cipher-algo AES256" in offsite
    # retention only after a verified upload
    assert offsite.index("drive_ops upload") < offsite.index("drive_ops rotate")
    restore = (BACKUP / "restore_check.sh").read_text(encoding="utf-8")
    assert "--network none" in restore and "--memory=600m" in restore
    assert "docker rm -f" in restore


def test_drive_ops_compiles_and_systemd_schedules():
    for name in ["drive_ops.py", "rotation.py"]:
        compile((BACKUP / name).read_text(encoding="utf-8"), name, "exec")
    backup_timer = (BACKUP / "pu-offsite-backup.timer").read_text(encoding="utf-8")
    restore_timer = (BACKUP / "pu-restore-check.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 03:30:00 Europe/Moscow" in backup_timer
    assert "OnCalendar=*-*-01 05:00:00 Europe/Moscow" in restore_timer
    for unit in ["pu-offsite-backup.service", "pu-restore-check.service"]:
        assert "Type=oneshot" in (BACKUP / unit).read_text(encoding="utf-8")
