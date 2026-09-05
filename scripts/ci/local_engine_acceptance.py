"""Local-only OCR and POSIX evidence, publishing counters rather than raw output."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from v54_pilot_workflow import SUMMARY_LINE


ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    "backend/tests/test_v54_ocr_benchmark.py::test_actual_local_tesseract_benchmark_gate",
    "backend/tests/test_v54_staging_filesystem.py::test_symlink_root_and_shard_are_rejected",
    "backend/tests/test_v54_staging_filesystem.py::test_symlink_final_and_partial_are_rejected",
)


def evaluate(code: int, output: str) -> dict:
    import re

    matches = [SUMMARY_LINE.fullmatch(line.strip()) for line in output.splitlines()]
    matches = [match for match in matches if match]
    counts = ({label: int(count) for count, label in re.findall(
        r"(\d+) ([a-z]+)", matches[-1].group("counts"))} if matches else {})
    passed, skipped = counts.get("passed", 0), counts.get("skipped", 0)
    success = (code == 0 and passed == len(TARGETS) and skipped == 0
               and not any(counts.get(key, 0) for key in
                           ("failed", "error", "errors", "xfailed", "xpassed", "deselected")))
    return {"schema": "puw.local-engines.v1", "result": "PASS" if success else "FAIL",
            "required": len(TARGETS), "passed": passed, "skipped": skipped,
            "raw_output_published": False, "external_providers": "NOT_USED"}


def child_env() -> dict:
    env = dict(os.environ)
    for key in tuple(env):
        if (key.startswith("PYTEST_") or key.startswith("PUW_")
                or key in {"TEST_POSTGRES_DSN", "TESSERACT_CMD", "OCR_BENCHMARK_FONT"}):
            env.pop(key)
    env.update(PYTHONPATH=str(ROOT / "backend"), PYTHONUTF8="1",
               DATABASE_URL="sqlite+pysqlite:///:memory:", PU_TEST_POSTGRES="0",
               OCR_ENABLED="true", OCR_EXTERNAL_VISION_ENABLED="false",
               GMAIL_AUTO_SYNC_ENABLED="false", AI_SECRETARY_AUTOMATION_ENABLED="false")
    return env


def main() -> int:
    protocol = evaluate(1, "")
    try:
        with tempfile.TemporaryDirectory(prefix="puw-local-engine-") as test_temp:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "pytest", *TARGETS,
                 "-q", "--tb=no", "-p", "no:cacheprovider", "--basetemp", test_temp],
                cwd=ROOT, env=child_env(), capture_output=True, text=True,
                errors="replace", timeout=900,
            )
        protocol = evaluate(result.returncode, result.stdout)
    except (OSError, subprocess.SubprocessError):
        pass  # No raw error/path/output enters logs or artifacts.
    output = ROOT / "local-engine-artifacts"
    output.mkdir(exist_ok=True)
    (output / "protocol.json").write_text(json.dumps(protocol) + "\n", encoding="utf-8")
    print("Local engine acceptance: " + protocol["result"])
    return 0 if protocol["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
