"""Reject a rendered Compose model that could escape the staging boundary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


APP_SERVICES = {"backend", "migrate", "worker", "scheduler"}
EXPECTED_SERVICES = APP_SERVICES | {"db", "gateway"}
EXTERNAL_KEYS = {
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "TELEGRAM_BOT_TOKEN",
    "GEMINI_API_KEY",
    "YANDEX_CLIENT_ID",
    "YANDEX_CLIENT_SECRET",
}


def network_names(value) -> set[str]:
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, list):
        return set(value)
    return set()


def validate(config: dict, project: str, revision: str, port: int, release: Path) -> None:
    if config.get("name") != project:
        raise ValueError("rendered Compose project name is not the staging project")
    services = config.get("services") or {}
    if set(services) != EXPECTED_SERVICES:
        raise ValueError("rendered Compose has an unexpected service set")

    expected_app_image = f"pu-workspace-staging:{revision}"
    for name, service in services.items():
        if service.get("container_name"):
            raise ValueError(f"{name} must not use a fixed container name")
        if service.get("privileged"):
            raise ValueError(f"{name} must not be privileged")
        namespace_keys = ("network_mode", "pid", "ipc", "uts", "userns_mode", "cgroup", "cgroup_parent", "runtime")
        if any(service.get(key) for key in namespace_keys):
            raise ValueError(f"{name} must not share host/container namespaces")
        if service.get("devices") or service.get("cap_add") or service.get("security_opt") or service.get("volumes_from"):
            raise ValueError(f"{name} requests elevated device or capability access")
        ports = service.get("ports") or []
        if name != "gateway" and ports:
            raise ValueError(f"{name} must not publish ports")
        image = service.get("image")
        if name in APP_SERVICES and image != expected_app_image:
            raise ValueError(f"{name} does not use the isolated staging image")

    if services["db"].get("image") != "postgres:16-alpine":
        raise ValueError("unexpected staging database image")
    if services["gateway"].get("image") != "nginx:1.28-alpine":
        raise ValueError("unexpected staging gateway image")

    ports = services["gateway"].get("ports") or []
    if len(ports) != 1:
        raise ValueError("staging gateway must publish exactly one port")
    published = ports[0]
    if (
        str(published.get("published")) != str(port)
        or int(published.get("target", 0)) != 8080
        or published.get("host_ip") != "127.0.0.1"
        or published.get("protocol", "tcp") != "tcp"
    ):
        raise ValueError("staging gateway port is not isolated on loopback")

    volumes = config.get("volumes") or {}
    data_volume = volumes.get("data") or {}
    if set(volumes) != {"data"} or data_volume.get("external"):
        raise ValueError("staging must use one private named volume")
    if data_volume.get("name") != f"{project}_data":
        raise ValueError("staging volume name does not belong to the staging project")

    db_mounts = services["db"].get("volumes") or []
    if (
        len(db_mounts) != 1
        or db_mounts[0].get("type") != "volume"
        or db_mounts[0].get("source") != "data"
        or db_mounts[0].get("target") != "/var/lib/postgresql/data"
    ):
        raise ValueError("database mount is not the private staging volume")
    gateway_mounts = services["gateway"].get("volumes") or []
    release_root = release.resolve()
    expected_nginx_path = release / "infra" / "ci" / "nginx.conf"
    expected_nginx = expected_nginx_path.resolve()
    if expected_nginx_path.is_symlink() or release_root not in expected_nginx.parents:
        raise ValueError("gateway configuration escapes the immutable release")
    if (
        len(gateway_mounts) != 1
        or gateway_mounts[0].get("type") != "bind"
        or Path(gateway_mounts[0].get("source", "")).resolve() != expected_nginx
        or gateway_mounts[0].get("target") != "/etc/nginx/conf.d/default.conf"
        or not gateway_mounts[0].get("read_only")
    ):
        raise ValueError("gateway bind mount escapes the release or is writable")
    for name in APP_SERVICES:
        if services[name].get("volumes"):
            raise ValueError(f"{name} must not mount host or Docker paths")

    networks = config.get("networks") or {}
    if set(networks) != {"default", "edge"}:
        raise ValueError("unexpected staging network set")
    if not (networks["default"] or {}).get("internal"):
        raise ValueError("application staging network must be internal")
    for name, network in networks.items():
        if (network or {}).get("external"):
            raise ValueError(f"{name} must not be an external network")
        if (network or {}).get("name") != f"{project}_{name}":
            raise ValueError(f"{name} network does not belong to the staging project")
    for name, service in services.items():
        expected = {"default", "edge"} if name == "gateway" else {"default"}
        if network_names(service.get("networks")) != expected:
            raise ValueError(f"{name} has unexpected network access")

    for name in APP_SERVICES:
        environment = services[name].get("environment") or {}
        for key in EXTERNAL_KEYS:
            if environment.get(key) not in {None, ""}:
                raise ValueError(f"{name} has external integration credential {key}")
        if environment.get("GMAIL_AUTO_SYNC_ENABLED") != "false":
            raise ValueError(f"{name} enables Gmail automation")
        if environment.get("AI_SECRETARY_AUTOMATION_ENABLED") != "false":
            raise ValueError(f"{name} enables AI Secretary automation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--release", required=True, type=Path)
    args = parser.parse_args()
    validate(json.load(sys.stdin), args.project, args.revision, args.port, args.release)
    print("Rendered Compose model is isolated for staging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
