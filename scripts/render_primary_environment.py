"""Render a private, release-pinned environment for a first-host primary stack."""
from __future__ import annotations

import argparse
import base64
import binascii
import os
from pathlib import Path
import re
import tempfile


REQUIRED_SECRETS = {
    "POSTGRES_PASSWORD": 24,
    "APP_SECRET_KEY": 32,
    "TOKEN_ENCRYPTION_KEY": 32,
    "BOOTSTRAP_TOKEN": 24,
}
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]{2,47}$")
SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254}$")
SAFE_SECRET = re.compile(r"^[A-Za-z0-9_.~+/=-]+$")


def validate_fernet_key(value: str) -> None:
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("TOKEN_ENCRYPTION_KEY must be a canonical Fernet key") from exc
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode("ascii") != value:
        raise ValueError("TOKEN_ENCRYPTION_KEY must be a canonical Fernet key")


def read_env(path: Path) -> dict[str, str]:
    if path.name != ".env.primary":
        raise ValueError("source must be a dedicated .env.primary file")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid primary env line {number}")
        key, value = line.split("=", 1)
        if not key or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in key):
            raise ValueError(f"invalid primary env key on line {number}")
        if key in values:
            raise ValueError(f"duplicate primary env key on line {number}")
        values[key] = value
    for key, minimum in REQUIRED_SECRETS.items():
        value = values.get(key, "")
        if (
            len(value) < minimum
            or not SAFE_SECRET.fullmatch(value)
            or "replace" in value.lower()
            or "change_me" in value.lower()
        ):
            raise ValueError(f"{key} is missing, too short, or a placeholder")
    validate_fernet_key(values["TOKEN_ENCRYPTION_KEY"])
    return values


def render(
    source: Path,
    target: Path,
    revision: str,
    project: str,
    port: int,
    image: str,
    volume: str,
    container_prefix: str,
    previous: Path | None = None,
) -> None:
    if target.name != ".env.primary" or source.resolve() == target.resolve():
        raise ValueError("target must be a separate .env.primary runtime file")
    revision = revision.lower()
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("revision must be a full commit SHA")
    for label, value in (("project", project), ("volume", volume), ("container prefix", container_prefix)):
        if not SAFE_NAME.fullmatch(value):
            raise ValueError(f"unsafe primary {label}")
    if project in {"app", "pu-workspace", "puw-staging"}:
        raise ValueError("primary project must not reuse an existing project")
    if not 1024 <= port <= 65535 or port in {3000, 3010, 443, 5678, 8080}:
        raise ValueError("primary loopback port is unsafe")
    if not SAFE_IMAGE.fullmatch(image):
        raise ValueError("candidate image reference is unsafe")
    values = read_env(source)
    if previous is not None:
        previous_values = read_env(previous)
        for key in ("POSTGRES_PASSWORD", "APP_SECRET_KEY", "TOKEN_ENCRYPTION_KEY"):
            if values[key] != previous_values[key]:
                raise ValueError(f"{key} rotation requires a separate migration procedure")
    values.update({
        "PRIMARY_CONTAINER_PREFIX": container_prefix,
        "PRIMARY_ENV_FILE": str(target),
        "PRIMARY_IMAGE": image,
        "PRIMARY_PORT": str(port),
        "PRIMARY_VOLUME_NAME": volume,
        "PU_RELEASE_REVISION": revision,
    })
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.primary.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            for key in sorted(values):
                output.write(f"{key}={values[key]}\n")
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--image", required=True)
    parser.add_argument("--volume", required=True)
    parser.add_argument("--container-prefix", required=True)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    render(
        args.source,
        args.output,
        args.revision,
        args.project,
        args.port,
        args.image,
        args.volume,
        args.container_prefix,
        args.previous,
    )
    print("Private primary runtime environment prepared; values were not logged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
