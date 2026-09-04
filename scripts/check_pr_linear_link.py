"""Validate that a pull request has one auditable Linear issue identity.

The check is intentionally local: it validates GitHub's event payload and never
calls Linear or GitHub. External integrations must still be verified separately.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


LINEAR_URL_RE = re.compile(
    r"https://linear\.app/pu-workspace-ai/issue/(?P<issue>PU-[1-9][0-9]*)(?:[/?#]|$)",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"^\[(?P<issue>PU-[1-9][0-9]*)\](?:\s|$)", re.IGNORECASE)


def _branch_issue(branch: str) -> str | None:
    match = re.fullmatch(
        r"codex/(?P<issue>pu-[1-9][0-9]*)[-_/].+",
        branch.strip(),
        re.IGNORECASE,
    )
    return match.group("issue").upper() if match else None


def validate_pull_request(payload: dict[str, Any]) -> list[str]:
    """Return actionable validation errors for a pull_request event payload."""

    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        return ["Event payload does not contain a pull_request object."]

    title = str(pull_request.get("title") or "").strip()
    body = str(pull_request.get("body") or "")
    head = pull_request.get("head")
    branch = str(head.get("ref") or "").strip() if isinstance(head, dict) else ""

    title_match = TITLE_RE.search(title)
    url_matches = list(LINEAR_URL_RE.finditer(body))
    branch_issue = _branch_issue(branch)
    errors: list[str] = []

    if not title_match:
        errors.append("PR title must start with a Linear key, for example: [PU-123] Short summary.")
    if not url_matches:
        errors.append(
            "PR body must contain a canonical Linear URL under linear.app/pu-workspace-ai/issue/PU-N."
        )
    if not branch_issue:
        errors.append("PR branch must use codex/pu-N-description and match the Linear issue key.")

    if errors:
        return errors

    title_issue = title_match.group("issue").upper()
    body_issues = {match.group("issue").upper() for match in url_matches}
    if len(body_issues) != 1:
        errors.append("PR body must reference exactly one Linear issue key.")
    elif title_issue not in body_issues or branch_issue not in body_issues:
        errors.append(
            "Linear issue key must be identical in PR title, PR branch and the canonical Linear URL."
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", default=os.getenv("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--event-path", default=os.getenv("GITHUB_EVENT_PATH", ""))
    args = parser.parse_args()

    if args.event_name != "pull_request":
        print("Linear linkage check skipped: event is not pull_request.")
        return 0
    if not args.event_path:
        parser.error("pull_request validation requires --event-path or GITHUB_EVENT_PATH")

    payload = json.loads(Path(args.event_path).read_text(encoding="utf-8"))
    errors = validate_pull_request(payload)
    if errors:
        for error in errors:
            print(f"::error title=Linear linkage::{error}")
        return 1

    pull_request = payload["pull_request"]
    issue = TITLE_RE.search(str(pull_request["title"])).group("issue").upper()
    print(f"Linear linkage verified: {issue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
