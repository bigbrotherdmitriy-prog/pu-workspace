"""Run pnpm audit without allowing one stalled registry request to consume the job."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


SEVERITIES = ("info", "low", "moderate", "high", "critical", "total")


def classify_payload(raw: str) -> str:
    """Return clean, vulnerable, or transient for a pnpm audit JSON response."""
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return "transient"

    counts = payload.get("metadata", {}).get("vulnerabilities")
    if not isinstance(counts, dict) or not all(key in counts for key in SEVERITIES):
        return "transient"
    if any(not isinstance(counts[key], int) for key in SEVERITIES):
        return "transient"
    return "vulnerable" if counts["high"] or counts["critical"] else "clean"


def audit_once(command: list[str], timeout: int, env: dict[str, str]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, stdout, stderr or f"audit timed out after {timeout} seconds"


def run(output: Path, attempts: int, timeout: int, sleep=time.sleep) -> int:
    env = os.environ.copy()
    env.update(
        {
            "npm_config_fetch_retries": "1",
            "npm_config_fetch_retry_mintimeout": "2000",
            "npm_config_fetch_retry_maxtimeout": "10000",
            "npm_config_fetch_timeout": "30000",
        }
    )
    command = ["pnpm", "--dir", "frontend", "audit", "--audit-level", "high", "--json"]

    for attempt in range(1, attempts + 1):
        returncode, stdout, stderr = audit_once(command, timeout, env)
        state = classify_payload(stdout)
        if stdout:
            output.write_text(stdout, encoding="utf-8")
        else:
            output.write_text(
                json.dumps(
                    {
                        "error": "frontend dependency audit unavailable",
                        "attempt": attempt,
                        "returncode": returncode,
                        "detail": stderr[-1000:],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        if state == "clean" and returncode == 0:
            print(f"frontend dependency audit passed on attempt {attempt}")
            return 0
        if state == "vulnerable":
            print("high or critical frontend dependency vulnerability found", file=sys.stderr)
            return 1

        print(
            f"frontend dependency audit attempt {attempt}/{attempts} unavailable "
            f"(exit {returncode}); registry request will be retried",
            file=sys.stderr,
        )
        if attempt < attempts:
            sleep(min(5 * attempt, 10))

    print("frontend dependency audit unavailable after bounded retries", file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 5:
        parser.error("--attempts must be between 1 and 5")
    if not 10 <= args.timeout <= 180:
        parser.error("--timeout must be between 10 and 180 seconds")
    return run(args.output, args.attempts, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
