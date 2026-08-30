from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


STATUSES = ("new", "contacted", "pilot", "closed", "rejected")


@dataclass(frozen=True)
class FunnelRow:
    source: str
    clicks: int
    leads: int
    statuses: dict[str, int]

    @property
    def conversion(self) -> str:
        return f"{self.leads / self.clicks * 100:.1f}%" if self.clicks else "—"


def source_from_uri(uri: str) -> str | None:
    parsed = urlsplit(uri)
    if parsed.path != "/go.html":
        return None
    source = parse_qs(parsed.query).get("source", [""])[0]
    if not source or len(source) > 64:
        return None
    if not all(character.isalnum() or character in "_-" for character in source):
        return None
    return source


def campaign_clicks(lines: list[str]) -> Counter[str]:
    clicks: Counter[str] = Counter()
    for line in lines:
        try:
            record = json.loads(line)
            uri = str(record.get("request", {}).get("uri", ""))
        except (json.JSONDecodeError, AttributeError):
            continue
        source = source_from_uri(uri)
        if source:
            clicks[source] += 1
    return clicks


def lead_counts(database: Path) -> dict[str, Counter[str]]:
    result: dict[str, Counter[str]] = {}
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT source, status, COUNT(*) FROM leads GROUP BY source, status"
        ).fetchall()
    finally:
        connection.close()
    for source, status, total in rows:
        if status not in STATUSES:
            continue
        result.setdefault(str(source), Counter())[str(status)] = int(total)
    return result


def funnel_rows(
    clicks: Counter[str], leads: dict[str, Counter[str]]
) -> list[FunnelRow]:
    rows = []
    for source in sorted(set(clicks) | set(leads)):
        statuses = {status: int(leads.get(source, {}).get(status, 0)) for status in STATUSES}
        rows.append(
            FunnelRow(
                source=source,
                clicks=int(clicks.get(source, 0)),
                leads=sum(statuses.values()),
                statuses=statuses,
            )
        )
    return sorted(rows, key=lambda row: (row.leads, row.clicks), reverse=True)


def render_report(rows: list[FunnelRow]) -> str:
    title = "PU Workspace — рекламная воронка"
    if not rows:
        return f"{title}\nДанных пока нет."
    headings = ("Метка", "Клики", "Заявки", "Конв.", "Новые", "Связались", "Пилот", "Закрыты", "Отказы")
    data = [
        (
            row.source,
            str(row.clicks),
            str(row.leads),
            row.conversion,
            str(row.statuses["new"]),
            str(row.statuses["contacted"]),
            str(row.statuses["pilot"]),
            str(row.statuses["closed"]),
            str(row.statuses["rejected"]),
        )
        for row in rows
    ]
    widths = [max(len(headings[index]), *(len(row[index]) for row in data)) for index in range(len(headings))]
    lines = [title, "  ".join(value.ljust(widths[index]) for index, value in enumerate(headings))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in data)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="PU Workspace campaign funnel report")
    parser.add_argument("log", type=Path, help="Caddy JSON access log")
    parser.add_argument("--database", type=Path, help="Sales bot SQLite database")
    args = parser.parse_args()
    clicks = campaign_clicks(args.log.read_text(encoding="utf-8").splitlines())
    leads = lead_counts(args.database) if args.database else {}
    print(render_report(funnel_rows(clicks, leads)))


if __name__ == "__main__":
    main()
