"""Read-only preflight for the PU Workspace ten-minute demonstration."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def readiness_steps(payload: dict) -> list[tuple[str, bool, str]]:
    documents = int(payload.get("documents") or 0)
    analyzed = int(payload.get("analyzed_documents") or 0)
    contracts = int(payload.get("contracts") or 0)
    linked = int(payload.get("linked_contracts") or 0)
    schedule = int(payload.get("schedule_rows") or 0)
    budget = int(payload.get("budget_rows") or 0)
    cash_flow = int(payload.get("cash_flow_rows") or 0)
    contacts = int(payload.get("contacts") or 0)
    confirmed = int(payload.get("confirmed_contacts") or 0)
    return [
        ("Безопасная рабочая копия", bool(payload.get("source_ready")) and documents > 0, f"документов: {documents}"),
        ("Анализ документов", analyzed > 0, f"обработано: {analyzed}"),
        ("Договор и документ-источник", contracts > 0 and linked == contracts, f"привязано: {linked} из {contracts}"),
        ("ГПР, бюджет и ДДС", schedule > 0 and budget > 0 and cash_flow > 0, f"ГПР: {schedule}; бюджет: {budget}; ДДС: {cash_flow}"),
        ("Маршрутизация почты", confirmed > 0, f"подтверждено контактов: {confirmed} из {contacts}"),
    ]


def fetch_readiness(base_url: str, project_id: int, token: str) -> dict:
    url = f"{base_url.rstrip('/')}/projects/{project_id}/launch-readiness"
    request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка готовности демонстрационного проекта без изменения данных")
    parser.add_argument("--base-url", default="http://localhost:3000/api", help="Базовый URL API, включая /api")
    parser.add_argument("--project-id", required=True, type=int)
    args = parser.parse_args()
    token = os.getenv("PU_WORKSPACE_TOKEN", "").strip()
    if not token:
        print("ERROR: задайте PU_WORKSPACE_TOKEN в окружении; токен не передавайте в аргументах команды.", file=sys.stderr)
        return 2
    try:
        payload = fetch_readiness(args.base_url, args.project_id, token)
    except HTTPError as exc:
        print(f"ERROR: API вернул HTTP {exc.code}.", file=sys.stderr)
        return 2
    except (URLError, TimeoutError, ValueError) as exc:
        print(f"ERROR: readiness API недоступен: {exc}", file=sys.stderr)
        return 2

    steps = readiness_steps(payload)
    print(f"PU Workspace · {payload.get('project_name') or args.project_id}")
    for title, complete, detail in steps:
        print(f"[{'OK' if complete else '  '}] {title}: {detail}")
    completed = sum(complete for _, complete, _ in steps)
    print(f"Готовность демонстрации: {completed}/{len(steps)} ({completed * 20}%)")
    return 0 if completed == len(steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
