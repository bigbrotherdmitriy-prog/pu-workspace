"""Wait until every required GitHub Actions check is successful for one SHA."""
from __future__ import annotations

import argparse
import json
import os
import time
from urllib.request import Request, urlopen


TERMINAL_FAILURES = {
    "action_required", "cancelled", "failure", "neutral", "skipped", "stale", "startup_failure", "timed_out",
}


def evaluate(check_runs: list[dict], required: set[str]) -> tuple[str, list[str]]:
    latest: dict[str, dict] = {}
    for run in check_runs:
        name = str(run.get("name", ""))
        if name in required and int(run.get("id", 0)) > int(latest.get(name, {}).get("id", 0)):
            latest[name] = run
    failed = sorted(
        name for name, run in latest.items() if run.get("status") == "completed" and run.get("conclusion") in TERMINAL_FAILURES
    )
    if failed:
        return "failed", failed
    pending = sorted(
        name for name in required if name not in latest
        or latest[name].get("status") != "completed"
        or latest[name].get("conclusion") != "success"
    )
    return ("pending", pending) if pending else ("success", [])


def fetch(owner_repo: str, sha: str, token: str) -> list[dict]:
    url = f"https://api.github.com/repos/{owner_repo}/commits/{sha}/check-runs?per_page=100"
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "PU-Workspace-Staging-Gate/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(request, timeout=30) as response:
        return json.load(response).get("check_runs", [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--check", action="append", required=True, dest="checks")
    parser.add_argument("--attempts", type=int, default=40)
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    if not args.sha or len(args.sha) != 40 or any(char not in "0123456789abcdef" for char in args.sha.lower()):
        parser.error("--sha must be a full 40-character commit SHA")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        parser.error("GITHUB_TOKEN is required")
    required = set(args.checks)
    for attempt in range(args.attempts):
        state, names = evaluate(fetch(args.repository, args.sha, token), required)
        if state == "success":
            print("All required release checks succeeded: " + ", ".join(sorted(required)))
            return 0
        if state == "failed":
            raise SystemExit("Required release checks failed: " + ", ".join(names))
        if attempt + 1 < args.attempts:
            time.sleep(args.interval)
    raise SystemExit("Timed out waiting for release checks: " + ", ".join(names))


if __name__ == "__main__":
    raise SystemExit(main())
