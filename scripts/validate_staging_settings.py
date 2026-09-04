"""Validate public staging settings before any SSH or Docker operation."""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import PurePosixPath
import re
import socket
from urllib.parse import urlparse


PRODUCTION_HOSTS = {
    "37.252.23.204",
    "pu-workspace.duckdns.org",
    "www.puworkspace.ru",
    "puworkspace.ru",
}
PRODUCTION_ADDRESSES = {"37.252.23.204"}
PRODUCTION_ROOT = PurePosixPath("/opt/pu-workspace")
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]{2,39}$")
SAFE_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SAFE_HOST = re.compile(r"^[A-Za-z0-9.-]+$")
SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")


def normalize_address(value: str) -> str:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def resolve_addresses(host: str, resolver) -> set[str]:
    try:
        addresses = {normalize_address(item[4][0]) for item in resolver(host, None, type=socket.SOCK_STREAM)}
    except (OSError, ValueError) as exc:
        raise ValueError(f"{host} does not resolve") from exc
    if not addresses:
        raise ValueError(f"{host} does not resolve")
    return addresses


def production_addresses(resolver) -> set[str]:
    addresses = set(PRODUCTION_ADDRESSES)
    for host in PRODUCTION_HOSTS - PRODUCTION_ADDRESSES:
        try:
            addresses.update(resolve_addresses(host, resolver))
        except ValueError:
            continue
    return addresses


def validate(values: dict[str, str], resolver=None) -> dict[str, str]:
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
    if host != values["STAGING_HOST"]:
        raise ValueError("STAGING_HOST must not contain surrounding whitespace")
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
    host_addresses: set[str] | None = None
    if resolver is not None:
        forbidden_addresses = production_addresses(resolver)
        host_addresses = resolve_addresses(host, resolver)
        if host_addresses & forbidden_addresses:
            raise ValueError("STAGING_HOST resolves to the production host")

    user = values["STAGING_USER"].strip()
    if user != values["STAGING_USER"]:
        raise ValueError("STAGING_USER must not contain surrounding whitespace")
    if not SAFE_USER.fullmatch(user) or user == "root":
        raise ValueError("STAGING_USER contains unsafe characters")

    root_text = values["STAGING_ROOT"].strip()
    if root_text != values["STAGING_ROOT"] or (root_text != "/" and root_text.endswith("/")):
        raise ValueError("STAGING_ROOT must be canonical and have no trailing slash")
    if not SAFE_PATH.fullmatch(root_text):
        raise ValueError("STAGING_ROOT must be an absolute safe POSIX path")
    root = PurePosixPath(root_text)
    if root in {PurePosixPath("/"), PurePosixPath("/opt"), PRODUCTION_ROOT}:
        raise ValueError("STAGING_ROOT must not be a production or broad system path")
    if PRODUCTION_ROOT in root.parents:
        raise ValueError("STAGING_ROOT must not be nested inside the production root")

    project = values["STAGING_PROJECT"].strip()
    if project != values["STAGING_PROJECT"]:
        raise ValueError("STAGING_PROJECT must not contain surrounding whitespace")
    if not SAFE_NAME.fullmatch(project) or project in {"app", "pu-workspace", "production"}:
        raise ValueError("STAGING_PROJECT must be a dedicated safe Compose project name")

    port_text = values["STAGING_PORT"]
    if not re.fullmatch(r"[1-9][0-9]{0,4}", port_text):
        raise ValueError("STAGING_PORT must be a canonical decimal port")
    port = int(port_text)
    if not 1024 <= port <= 65535 or port in {3000, 443}:
        raise ValueError("STAGING_PORT must be a non-production port from 1024 to 65535")
    ssh_port_text = values["STAGING_SSH_PORT"]
    if not re.fullmatch(r"[1-9][0-9]{0,4}", ssh_port_text):
        raise ValueError("STAGING_SSH_PORT must be a canonical decimal port")
    ssh_port = int(ssh_port_text)
    if not 1 <= ssh_port <= 65535:
        raise ValueError("STAGING_SSH_PORT must be from 1 to 65535")

    public_url = values["STAGING_PUBLIC_URL"].strip()
    if public_url != values["STAGING_PUBLIC_URL"] or public_url.endswith("/"):
        raise ValueError("STAGING_PUBLIC_URL must be canonical and have no trailing slash")
    parsed = urlparse(public_url)
    try:
        public_port = parsed.port
    except ValueError as exc:
        raise ValueError("STAGING_PUBLIC_URL contains an invalid port") from exc
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
        or public_port not in {None, 443}
    ):
        raise ValueError("STAGING_PUBLIC_URL must be a dedicated HTTPS origin")
    try:
        public_ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        public_ip = None
    if public_ip and (public_ip.is_loopback or public_ip.is_private or public_ip.is_unspecified):
        raise ValueError("STAGING_PUBLIC_URL must be publicly reachable")
    if resolver is not None:
        public_addresses = resolve_addresses(parsed.hostname, resolver)
        if public_addresses & forbidden_addresses:
            raise ValueError("STAGING_PUBLIC_URL resolves to the production host")
        for address in public_addresses:
            resolved_ip = ipaddress.ip_address(address)
            if resolved_ip.is_loopback or resolved_ip.is_private or resolved_ip.is_unspecified or resolved_ip.is_multicast:
                raise ValueError("STAGING_PUBLIC_URL must resolve to public addresses")

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
    validate(dict(os.environ), resolver=socket.getaddrinfo)
    print("Staging settings are valid; values were not logged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
