"""Validate public staging settings before any SSH or Docker operation."""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import PurePosixPath
import re
from urllib.parse import urlparse


PRODUCTION_HOSTS = {"pu-workspace.duckdns.org", "www.puworkspace.ru", "puworkspace.ru"}
PRODUCTION_ROOT = PurePosixPath("/opt/pu-workspace")
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]{2,39}$")
SAFE_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SAFE_HOST = re.compile(r"^[A-Za-z0-9.-]+$")
SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")


def validate(values: dict[str, str]) -> dict[str, str]:
    required = (
        "STAGING_HOST",
        "STAGING_USER",
        "STAGING_ROOT",
        "STAGING_PROJECT",
        "STAGING_PORT",
        "STAGING_PUBLIC_URL",
        "STAGING_SSH_PORT",
    )
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise ValueError("missing staging settings: " + ", ".join(missing))

    host = values["STAGING_HOST"].strip()
    if not SAFE_HOST.fullmatch(host) or host.lower() in PRODUCTION_HOSTS:
        raise ValueError("STAGING_HOST must be a dedicated staging hostname or IP")
    try:
        host_ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or ".." in host:
            raise ValueError("STAGING_HOST is not a valid hostname")
    else:
        if host_ip.is_loopback or host_ip.is_unspecified or host_ip.is_multicast:
            raise ValueError("STAGING_HOST must be a reachable staging host")

    user = values["STAGING_USER"].strip()
    if not SAFE_USER.fullmatch(user):
        raise ValueError("STAGING_USER contains unsafe characters")

    root_text = values["STAGING_ROOT"].strip().rstrip("/")
    if not SAFE_PATH.fullmatch(root_text):
        raise ValueError("STAGING_ROOT must be an absolute safe POSIX path")
    root = PurePosixPath(root_text)
    if root in {PurePosixPath("/"), PurePosixPath("/opt"), PRODUCTION_ROOT}:
        raise ValueError("STAGING_ROOT must not be a production or broad system path")
    if PRODUCTION_ROOT in root.parents:
        raise ValueError("STAGING_ROOT must not be nested inside the production root")

    project = values["STAGING_PROJECT"].strip()
    if not SAFE_NAME.fullmatch(project) or project in {"app", "pu-workspace", "production"}:
        raise ValueError("STAGING_PROJECT must be a dedicated safe Compose project name")

    port = int(values["STAGING_PORT"])
    if not 1024 <= port <= 65535 or port in {3000, 443}:
        raise ValueError("STAGING_PORT must be a non-production port from 1024 to 65535")
    ssh_port = int(values["STAGING_SSH_PORT"])
    if not 1 <= ssh_port <= 65535:
        raise ValueError("STAGING_SSH_PORT must be from 1 to 65535")

    public_url = values["STAGING_PUBLIC_URL"].strip().rstrip("/")
    parsed = urlparse(public_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not SAFE_HOST.fullmatch(parsed.hostname)
        or parsed.hostname.lower() in PRODUCTION_HOSTS
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("STAGING_PUBLIC_URL must be a dedicated HTTPS origin")
    try:
        public_ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        public_ip = None
    if public_ip and (public_ip.is_loopback or public_ip.is_private or public_ip.is_unspecified):
        raise ValueError("STAGING_PUBLIC_URL must be publicly reachable")

    return {
        "STAGING_HOST": host,
        "STAGING_USER": user,
        "STAGING_ROOT": str(root),
        "STAGING_PROJECT": project,
        "STAGING_PORT": str(port),
        "STAGING_PUBLIC_URL": public_url,
        "STAGING_SSH_PORT": str(ssh_port),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", default="staging")
    args = parser.parse_args()
    if args.environment != "staging":
        parser.error("only the staging environment is accepted")
    validate(dict(os.environ))
    print("Staging settings are valid; values were not logged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
