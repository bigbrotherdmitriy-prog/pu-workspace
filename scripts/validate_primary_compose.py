"""Fail closed when the rendered first-host primary Compose model is not isolated."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


APP_SERVICES = {"backend", "worker", "scheduler"}
EXPECTED_SERVICES = APP_SERVICES | {"db"}


def fail(message: str) -> None:
    raise ValueError(message)


def validate(model: dict, project: str, image: str, port: int, volume: str, prefix: str) -> None:
    if model.get("name") != project:
        fail("rendered Compose project does not match the requested primary project")
    services = model.get("services") or {}
    if set(services) != EXPECTED_SERVICES:
        fail("first-host primary Compose must contain only db, backend, workers and scheduler")
    for name, service in services.items():
        if service.get("privileged") or service.get("network_mode") == "host":
            fail(f"unsafe container privileges or host networking in {name}")
        if name in APP_SERVICES and service.get("image") != image:
            fail(f"service {name} does not use the requested candidate image")
    backend_ports = services["backend"].get("ports") or []
    if len(backend_ports) != 1:
        fail("backend must publish exactly one loopback port")
    published = backend_ports[0]
    if (
        published.get("host_ip") != "127.0.0.1"
        or str(published.get("published")) != str(port)
        or int(published.get("target", 0)) != 8000
    ):
        fail("backend may only publish the requested loopback port")
    for name, service in services.items():
        if name != "backend" and service.get("ports"):
            fail(f"service {name} must not publish a host port")
    expected_names = {
        "db": f"{prefix}-db",
        "backend": f"{prefix}-backend",
    }
    for name, expected in expected_names.items():
        if services[name].get("container_name") != expected:
            fail(f"service {name} has an unexpected container name")
    volumes = model.get("volumes") or {}
    if set(volumes) != {"data"} or volumes["data"].get("name") != volume:
        fail("database volume is not the dedicated primary volume")
    # OAuth redirect configuration may legitimately retain the production URL so
    # encrypted tokens remain usable after DNS cutover. Only executable Compose
    # fields are forbidden from contacting the old endpoint during preparation.
    executable = json.dumps({
        name: {
            "command": service.get("command"),
            "entrypoint": service.get("entrypoint"),
            "healthcheck": service.get("healthcheck"),
        }
        for name, service in services.items()
    }, sort_keys=True)
    for forbidden in ("pu-workspace.duckdns.org", "puworkspace.ru", "37.252.23.204"):
        if forbidden in executable:
            fail("candidate Compose executable fields refer to the old production endpoint")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--volume", required=True)
    parser.add_argument("--container-prefix", required=True)
    args = parser.parse_args()
    validate(json.load(sys.stdin), args.project, args.image, args.port, args.volume, args.container_prefix)
    print("Rendered primary Compose isolation validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
