#!/usr/bin/env python3
"""Read-only MVP5 pilot preflight. Never creates external actions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _get(base_url: str, path: str, token: str | None = None) -> dict:
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def evaluate(core: dict, integrations: dict, launch: dict, briefing: dict) -> list[tuple[str, bool, str]]:
    adapters = integrations.get("adapters", [])
    available = [row for row in adapters if row.get("available")]
    connected = [row for row in available if row.get("connected")]
    steps = launch.get("steps", [])
    completed_steps = int(launch.get("completed_steps") or sum(bool(row.get("complete")) for row in steps))
    total_steps = int(launch.get("total_steps") or len(steps))
    launch_ready = (
        bool(launch.get("ready"))
        if "ready" in launch
        else total_steps > 0 and completed_steps == total_steps
    )
    return [
        ("Core и БД", bool(core.get("ready")), "readiness API"),
        ("Каталог адаптеров", bool(available), f"доступно {len(available)}, подключено {len(connected)}"),
        ("Запуск проекта", launch_ready, f"готово {completed_steps} из {total_steps}"),
        ("AI Secretary", briefing.get("external_actions_created") is False, f"требуют внимания {briefing.get('summary', {}).get('attention', 0)}"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка PU Workspace перед пилотом")
    parser.add_argument("--base-url", required=True, help="Например https://puworkspace.ru")
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--token", default=os.getenv("PU_WORKSPACE_TOKEN"))
    args = parser.parse_args()
    if not args.token:
        print("ERROR: задайте PU_WORKSPACE_TOKEN или --token", file=sys.stderr)
        return 2

    try:
        core = _get(args.base_url, "/api/readiness")
        integrations = _get(args.base_url, f"/integrations/project?project_id={args.project_id}", args.token)
        launch = _get(args.base_url, f"/projects/{args.project_id}/launch-readiness", args.token)
        briefing = _get(args.base_url, f"/ai-secretary/daily-briefing?project_id={args.project_id}", args.token)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"ERROR: preflight API недоступен: {exc}", file=sys.stderr)
        return 2

    results = evaluate(core, integrations, launch, briefing)
    for name, ok, detail in results:
        print(f"{'OK' if ok else 'FAIL'}  {name}: {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
