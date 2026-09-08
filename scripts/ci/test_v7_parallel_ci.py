"""Preserve complete checks while splitting independent CI runners."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf8"))


def commands(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_independent_jobs_and_existing_required_gate_identity():
    jobs = workflow()["jobs"]
    assert set(jobs) == {"backend", "frontend", "test-and-build"}
    assert "needs" not in jobs["backend"] and "needs" not in jobs["frontend"]
    gate = jobs["test-and-build"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"backend", "frontend"}
    assert all(not job.get("continue-on-error") for job in jobs.values())
    assert all(not step.get("continue-on-error") for job in jobs.values() for step in job["steps"])


def test_migration_backend_and_frontend_commands_not_reduced():
    jobs = workflow()["jobs"]
    backend = jobs["backend"]
    assert backend["services"]["postgres"]["image"] == "postgres:16-alpine"
    assert backend["env"]["PU_TEST_POSTGRES"] == "1"
    assert "pu_workspace_test" in backend["env"]["DATABASE_URL"]
    text = commands(backend)
    assert text.index("alembic -c alembic.ini upgrade head") < text.index("pytest tests -q")
    assert "pip install -r backend/requirements.txt" in text
    assert "pytest tests -q 2>&1 | tee ../pytest.log" in text
    assert "services" not in jobs["frontend"] and "env" not in jobs["frontend"]
    frontend = commands(jobs["frontend"])
    for command in ("pnpm install --frozen-lockfile", "pnpm run check", "pnpm run test", "pnpm run build"):
        assert command in frontend
    assert text.count("set -o pipefail") == 1
    assert frontend.count("set -o pipefail") == 3
    assert "-k " not in text and "--ignore" not in text and "--deselect" not in text


def test_artifacts_belong_to_their_runner_and_cache_remains():
    jobs = workflow()["jobs"]
    names = []
    for key, paths, cache in (("backend", {"pytest.log"}, "pip"), ("frontend", {"frontend-check.log", "frontend-test.log", "frontend-build.log"}, "pnpm")):
        steps = jobs[key]["steps"]
        upload = next(s for s in steps if s.get("uses") == "actions/upload-artifact@v4")
        assert upload["if"] == "always()"
        assert set(upload["with"]["path"].split()) == paths
        assert "github.run_id" in upload["with"]["name"] and "github.run_attempt" in upload["with"]["name"]
        names.append(upload["with"]["name"])
        assert any(s.get("with", {}).get("cache") == cache for s in steps)
        assert next(s for s in steps if s.get("uses") == "actions/checkout@v4")["with"]["persist-credentials"] is False
    assert len(set(names)) == 2


@pytest.mark.parametrize("needs,expected", [
    ({"backend": {"result": "success"}, "frontend": {"result": "success"}}, 0),
    ({"backend": {"result": "success"}}, 1),
    ({"backend": {"result": "success"}, "frontend": {"result": "skipped"}}, 1),
    ({"backend": {"result": "failure"}, "frontend": {"result": "success"}}, 1),
    ({"backend": {"result": "cancelled"}, "frontend": {"result": "success"}}, 1),
    ({"backend": {"result": "success"}, "frontend": {}}, 1),
    ({"backend": {"result": "success"}, "frontend": {"result": "success"}, "unknown": {}}, 1),
    ([], 1),
    (None, 1),
    ({"backend": "success", "frontend": {"result": "success"}}, 1),
    ({"backend": {"result": True}, "frontend": {"result": "success"}}, 1),
    ("not-json-object", 1),
])
def test_exact_gate_runs_fail_closed(needs, expected):
    gate = workflow()["jobs"]["test-and-build"]
    step = next(s for s in gate["steps"] if "run" in s)
    assert step["env"]["CI_NEEDS_JSON"] == "${{ toJSON(needs) }}"
    script = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "CI_NEEDS_JSON": json.dumps(needs)}, capture_output=True, text=True, timeout=5)
    assert result.returncode == expected
    assert result.stdout.strip() == ("CI aggregate PASS" if expected == 0 else "CI aggregate FAIL")
    assert not result.stderr
