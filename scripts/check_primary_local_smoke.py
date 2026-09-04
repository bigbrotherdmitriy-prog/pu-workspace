"""Read-only smoke test for a first-host primary candidate over loopback only."""
from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen


def read_json(base: str, path: str) -> dict:
    with urlopen(base + path, timeout=5) as response:
        return json.load(response)


def candidate_readiness_acceptable(readiness: dict) -> bool:
    if readiness.get("ready"):
        return True
    checks = readiness.get("checks")
    if not isinstance(checks, dict):
        return False
    failed_required = {
        name
        for name, check in checks.items()
        if isinstance(check, dict) and check.get("required") and not check.get("ok")
    }
    return failed_required == {"durable_workers", "durable_scheduler"}


def run(base: str, expected_release: str) -> None:
    parsed = urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("primary candidate smoke is restricted to loopback HTTP")
    if not parsed.port or parsed.port in {3000, 3010, 443}:
        raise ValueError("primary candidate smoke port is missing or unsafe")
    last_error = "not ready"
    for _attempt in range(60):
        try:
            readiness = read_json(base, "/api/readiness")
            if candidate_readiness_acceptable(readiness):
                break
            last_error = "unexpected required readiness failure"
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            last_error = type(error).__name__
        time.sleep(2)
    else:
        raise RuntimeError(f"candidate readiness did not become green: {last_error}")
    assert read_json(base, "/health").get("status") == "healthy"
    status = read_json(base, "/api/status")
    assert status.get("release") == expected_release, "wrong release is listening on the candidate port"
    try:
        read_json(base, "/projects/")
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("anonymous project access is allowed")
    print(json.dumps({"ready": True, "release": expected_release, "loopback_only": True}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--expected-release", required=True)
    args = parser.parse_args()
    run(f"http://127.0.0.1:{args.port}", args.expected_release)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
