"""The real outbox races are opt-in locally and mandatory against migrated PG in CI."""
from pathlib import Path
import shutil
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_outbox_recovery_races_require_isolated_migrated_postgres_without_skips():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf8"))
    job = workflow["jobs"]["test-and-build"]
    assert any("pip install -r backend/requirements.txt PyYAML" in step.get("run", "")
               for step in job["steps"]), "The workflow contract test needs PyYAML in CI"
    step = next(step for step in job["steps"]
                if step.get("name") == "Provider outbox PostgreSQL recovery races")
    assert "if" not in step and "continue-on-error" not in step
    assert step["working-directory"] == "backend"
    assert step["env"]["DATABASE_URL"] == step["env"]["PUW_TRACK_E_TEST_DSN"]
    assert step["env"]["DATABASE_URL"].endswith("@127.0.0.1:5432/puw_track_e_test")
    assert job["services"]["postgres"]["env"]["POSTGRES_DB"] != "puw_track_e_test"
    script = step["run"]
    assert script.index("CREATE DATABASE puw_track_e_test") < script.index("alembic") < script.index("pytest")
    assert "tests/test_mvp2_provider_outbox_postgres.py" in script
    assert "--junitxml=../provider-outbox-postgres.xml" in script
    assert "assert report.findall('.//testcase')" in script
    assert "assert not report.findall('.//skipped')" in script
    assert "set -o pipefail" in script
    upload = next(step for step in job["steps"] if step.get("name") == "Upload verification logs")
    assert "provider-outbox-postgres.log" in upload["with"]["path"]
    assert "provider-outbox-postgres.xml" in upload["with"]["path"]
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    assert bash, "Bash is required to validate the PostgreSQL CI gate"
    result = subprocess.run([bash, "--noprofile", "--norc", "-n"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
