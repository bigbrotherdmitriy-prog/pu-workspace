from __future__ import annotations

import argparse
import json
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit


def sitemap_urls(path: Path, host: str) -> list[str]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    urls = []
    for location in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        value = (location.text or "").strip()
        if value and urlsplit(value).hostname == host:
            urls.append(value)
    return urls


def payload(host: str, key: str, urls: list[str]) -> dict[str, object]:
    return {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls,
    }


def submit(endpoint: str, data: dict[str, object]) -> int:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return int(response.status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit changed PU Workspace pages to IndexNow")
    parser.add_argument("sitemap", type=Path)
    parser.add_argument("--host", default="puworkspace.ru")
    parser.add_argument("--key", required=True)
    parser.add_argument("--endpoint", default="https://yandex.com/indexnow")
    args = parser.parse_args()
    urls = sitemap_urls(args.sitemap, args.host)
    if not urls:
        raise SystemExit("No same-host URLs found in sitemap")
    status = submit(args.endpoint, payload(args.host, args.key, urls))
    if status not in {200, 202}:
        raise SystemExit(f"IndexNow returned HTTP {status}")
    print(f"INDEXNOW_ACCEPTED status={status} urls={len(urls)}")


if __name__ == "__main__":
    main()
