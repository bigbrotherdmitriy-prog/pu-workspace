"""Offline regression and fail-closed CI aggregation; safe counters only."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_JOBS = ("backend-offline", "runtime", "local-engines", "lint")
SUMMARY = re.compile(
    r"^(?:=+ )?(?P<counts>\d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|errors?|warnings?)"
    r"(?:, \d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|errors?|warnings?))*)"
    r" in [0-9.]+s(?: \([^\r\n]*\))?(?: =+)?$"
)


def offline_env() -> dict[str, str]:
    # OS plumbing only: no inherited provider, PostgreSQL or pytest configuration.
    allowed = ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
               "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(PYTHONPATH=str(ROOT / "backend"), PYTHONUTF8="1",
               DATABASE_URL="sqlite+pysqlite:///:memory:", PU_TEST_POSTGRES="0",
               GMAIL_AUTO_SYNC_ENABLED="false", AI_SECRETARY_AUTOMATION_ENABLED="false",
               OCR_EXTERNAL_VISION_ENABLED="false", APP_SECRET_KEY=secrets.token_hex(32),
               BOOTSTRAP_TOKEN=secrets.token_hex(32),
               TOKEN_ENCRYPTION_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"))
    return env


def evaluate_offline(code: int, output: str) -> dict:
    matches = [match for line in output.splitlines() if (match := SUMMARY.fullmatch(line.strip()))]
    counts = ({label: int(count) for count, label in re.findall(
        r"(\d+) ([a-z]+)", matches[-1].group("counts"))} if matches else {})
    success = code == 0 and counts.get("passed", 0) > 0 and not any(
        counts.get(key, 0) for key in ("failed", "error", "errors", "xfailed", "xpassed", "deselected"))
    return {
        "schema": "puw.v7.backend-offline.v1", "result": "PASS" if success else "FAIL",
        "scope": "full_backend_offline_regression", "passed": counts.get("passed", 0),
        "skipped": counts.get("skipped", 0), "postgres_runtime": "NOT_RUN",
        "skip_policy": "reported_not_acceptance; mandatory_postgres_and_local_engines_are_separate_jobs",
        "live_provider_validation": "NOT_RUN", "provider_credentials": "NOT_INHERITED",
        "raw_output_published": False,
    }


def evaluate_gate(needs: object) -> dict:
    needs = needs if isinstance(needs, dict) else {}
    allowed = {"success", "failure", "cancelled", "skipped"}
    states = {}
    for key in REQUIRED_JOBS:
        row = needs.get(key)
        value = row.get("result") if isinstance(row, dict) else None
        states[key] = value if isinstance(value, str) and value in allowed else "MISSING"
    success = set(needs) == set(REQUIRED_JOBS) and all(value == "success" for value in states.values())
    return {"schema": "puw.v7.ci-capacity-gate.v1", "result": "PASS" if success else "FAIL",
            "jobs": states, "scope": "isolated_ci_only_not_product_release_acceptance",
            "raw_output_published": False}


def publish(protocol: dict, directory: str) -> int:
    commit = os.environ.get("GITHUB_SHA", "")
    protocol["commit"] = commit if re.fullmatch(r"[0-9a-f]{40}", commit) else "local"
    output = ROOT / directory
    try:
        output.mkdir(exist_ok=True)
        (output / "protocol.json").write_text(json.dumps(protocol, sort_keys=True) + "\n", encoding="utf8")
    except OSError:
        print("CI protocol publication: FAIL")
        return 1  # Missing artifact also fails upload; never print raw path/error.
    print("CI scope result: " + protocol["result"])
    return 0 if protocol["result"] == "PASS" else 1


def run_offline() -> int:
    protocol = evaluate_offline(1, "")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="puw-backend-offline-") as test_temp:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "pytest", "backend/tests", "-q", "--tb=short", "-rs",
                 "-p", "no:cacheprovider", "--basetemp", test_temp],
                cwd=ROOT, env=offline_env(), capture_output=True, text=True, errors="replace", timeout=900,
            )
        protocol = evaluate_offline(result.returncode, result.stdout)
    except (OSError, subprocess.SubprocessError):
        pass  # No exception text, stderr, traceback or partial timeout output.
    protocol["seconds"] = round(time.monotonic() - started, 2)
    return publish(protocol, "v7-backend-offline-artifacts")


def run_gate() -> int:
    try:
        needs = json.loads(os.environ.get("CI_NEEDS_JSON", ""))
    except (ValueError, TypeError):
        needs = None
    return publish(evaluate_gate(needs), "v7-ci-capacity-artifacts")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("offline", "gate"))
    mode = parser.parse_args().mode
    raise SystemExit(run_offline() if mode == "offline" else run_gate())
