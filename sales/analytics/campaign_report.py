from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


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


def main() -> None:
    parser = argparse.ArgumentParser(description="PU Workspace campaign click report")
    parser.add_argument("log", type=Path, help="Caddy JSON access log")
    args = parser.parse_args()
    clicks = campaign_clicks(args.log.read_text(encoding="utf-8").splitlines())
    print("PU Workspace — переходы в Telegram")
    if not clicks:
        print("Переходов пока нет.")
        return
    for source, total in clicks.most_common():
        print(f"{source}: {total}")


if __name__ == "__main__":
    main()
