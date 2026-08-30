from __future__ import annotations

import json
import re
import sys
from urllib.parse import urljoin
from urllib.request import Request, urlopen


REQUIRED_UI_MARKERS = (
    "Запуск проекта",
    "Договоры",
    "Исполнение и финансы",
    "Письма",
)


def _get(url: str, timeout: int = 20) -> tuple[int, str]:
    request = Request(url, headers={"User-Agent": "PU-Workspace-Deploy-Smoke/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return int(response.status), response.read().decode("utf-8", errors="replace")


def check_public(base_url: str) -> dict[str, object]:
    base = base_url.rstrip("/") + "/"
    readiness_url = urljoin(base, "api/readiness")
    app_url = urljoin(base, "new/")

    readiness_status, readiness_text = _get(readiness_url)
    readiness = json.loads(readiness_text)
    if readiness_status != 200 or not readiness.get("ready"):
        raise RuntimeError("public readiness is not green")

    html_status, html = _get(app_url)
    if html_status != 200 or 'id="root"' not in html:
        raise RuntimeError("public SPA root is unavailable")
    asset_match = re.search(r'<script[^>]+src="([^"]+\.js)"', html)
    if not asset_match:
        raise RuntimeError("public SPA JavaScript asset was not found")
    asset_url = urljoin(app_url, asset_match.group(1))
    asset_status, bundle = _get(asset_url)
    if asset_status != 200:
        raise RuntimeError("public SPA JavaScript asset is unavailable")
    missing = [marker for marker in REQUIRED_UI_MARKERS if marker not in bundle]
    if missing:
        raise RuntimeError("public SPA is incomplete or stale: " + ", ".join(missing))

    return {
        "ready": True,
        "app_url": app_url,
        "asset_url": asset_url,
        "markers": len(REQUIRED_UI_MARKERS),
    }


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "https://pu-workspace.duckdns.org/"
    try:
        result = check_public(base_url)
    except Exception as exc:
        print(f"public smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
