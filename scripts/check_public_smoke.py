from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen


REQUIRED_UI_MARKERS = (
    "Запуск проекта",
    "Договоры",
    "Исполнение и финансы",
    "Письма",
)


def _get(url: str, timeout: int = 20, token: str | None = None) -> tuple[int, str]:
    headers = {"User-Agent": "PU-Workspace-Deploy-Smoke/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return int(response.status), response.read().decode("utf-8", errors="replace")


def check_public(
    base_url: str,
    expected_asset: str | None = None,
    expected_release: str | None = None,
) -> dict[str, object]:
    base = base_url.rstrip("/") + "/"
    readiness_url = urljoin(base, "api/readiness")
    app_url = urljoin(base, "new/")

    readiness_status, readiness_text = _get(readiness_url)
    readiness = json.loads(readiness_text)
    if readiness_status != 200 or not readiness.get("ready"):
        raise RuntimeError("public readiness is not green")

    status_code, status_text = _get(urljoin(base, "api/status"))
    status = json.loads(status_text)
    active_release = status.get("release")
    if status_code != 200 or status.get("status") != "ok":
        raise RuntimeError("public backend status is unavailable")
    if expected_release and active_release != expected_release:
        raise RuntimeError(
            f"public backend release is stale: expected {expected_release}, got {active_release}"
        )

    html_status, html = _get(app_url)
    if html_status != 200 or 'id="root"' not in html:
        raise RuntimeError("public SPA root is unavailable")
    asset_match = re.search(r'<script[^>]+src="([^"]+\.js)"', html)
    if not asset_match:
        raise RuntimeError("public SPA JavaScript asset was not found")
    asset_url = urljoin(app_url, asset_match.group(1))
    public_asset = asset_url.rsplit("/", 1)[-1]
    if expected_asset and public_asset != expected_asset:
        raise RuntimeError(
            f"public SPA asset is stale: expected {expected_asset}, got {public_asset}"
        )
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
        "release": active_release,
    }


def check_authenticated_flow(base_url: str, token: str) -> dict[str, object]:
    """Read-only acceptance check for the deployed project workflow."""
    base = base_url.rstrip("/") + "/"
    projects_status, projects_text = _get(urljoin(base, "projects/"), token=token)
    projects_payload = json.loads(projects_text)
    projects = projects_payload.get("projects", projects_payload if isinstance(projects_payload, list) else [])
    if projects_status != 200 or not projects:
        raise RuntimeError("authenticated project catalog is unavailable or empty")
    project_id = int(projects[0]["id"])
    readiness_status, readiness_text = _get(
        urljoin(base, f"projects/{project_id}/launch-readiness"), token=token,
    )
    dashboard_status, dashboard_text = _get(
        urljoin(base, f"dashboard/project?project_id={project_id}"), token=token,
    )
    readiness = json.loads(readiness_text)
    dashboard = json.loads(dashboard_text)
    if readiness_status != 200 or readiness.get("project_id") not in {None, project_id}:
        raise RuntimeError("project launch readiness is unavailable")
    if dashboard_status != 200 or "summary" not in dashboard:
        raise RuntimeError("project dashboard is unavailable")
    return {
        "project_id": project_id,
        "project_name": projects[0].get("name", ""),
        "launch_readiness": True,
        "dashboard": True,
    }


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "https://pu-workspace.duckdns.org/"
    try:
        expected_asset = None
        image_index = Path("/app/app/react_dist/index.html")
        if image_index.is_file():
            image_html = image_index.read_text(encoding="utf-8")
            image_match = re.search(r'<script[^>]+src="([^"]+\.js)"', image_html)
            if not image_match:
                raise RuntimeError("candidate SPA JavaScript asset was not found")
            expected_asset = image_match.group(1).rsplit("/", 1)[-1]
        result = check_public(
            base_url,
            expected_asset=expected_asset,
            expected_release=os.getenv("PU_EXPECTED_RELEASE", "").strip() or None,
        )
        token = os.getenv("PU_WORKSPACE_TOKEN", "").strip()
        if token:
            result["authenticated"] = check_authenticated_flow(base_url, token)
    except Exception as exc:
        print(f"public smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
