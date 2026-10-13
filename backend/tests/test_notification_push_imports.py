"""Every process entry point must import with the push outbox hook installed."""
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", [
    "app.jobs.worker", "app.jobs.scheduler", "app.jobs.queue", "app.main", "app.models",
])
def test_entry_point_imports_in_a_fresh_interpreter(module):
    # The worker and scheduler start from app.jobs, which imports app.models,
    # which loads app.notification_push; a cycle there kills both processes.
    code = f"import {module}, app.notification_push, app.models.management as m, sqlalchemy.event as e; " \
           "assert e.contains(m.Notification, 'after_insert', app.notification_push._queue_push_for_new_notification)"
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True,
                            env={"PYTHONPATH": str(BACKEND), "DATABASE_URL": "sqlite+pysqlite:///:memory:",
                                 "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr[-2000:]
