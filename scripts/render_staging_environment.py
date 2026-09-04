"""Create a private runtime env from a dedicated staging secret file."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import tempfile


REQUIRED_SECRETS = {
    "POSTGRES_PASSWORD": 24,
    "APP_SECRET_KEY": 32,
    "TOKEN_ENCRYPTION_KEY": 32,
    "BOOTSTRAP_TOKEN": 24,
    "PU_SMOKE_PASSWORD": 20,
}
SAFE_SECRET = re.compile(r"^[A-Za-z0-9_.~+/=-]+$")


def read_env(path: Path) -> dict[str, str]:
    if path.name != ".env.staging":
        raise ValueError("source must be a dedicated .env.staging file")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid staging env line {number}")
        key, value = line.split("=", 1)
        if not key or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in key):
            raise ValueError(f"invalid staging env key on line {number}")
        if "\n" in value or "\r" in value:
            raise ValueError(f"invalid staging env value on line {number}")
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
    return values


def render(source: Path, target: Path, revision: str, port: int) -> None:
    if target.name != ".env.staging" or source.resolve() == target.resolve():
        raise ValueError("target must be a separate .env.staging runtime file")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision.lower()):
        raise ValueError("revision must be a full commit SHA")
    if not 1024 <= port <= 65535 or port in {3000, 443}:
        raise ValueError("staging port is unsafe")
    values = read_env(source)
    values.update({
        "PU_RELEASE_REVISION": revision.lower(),
        "PU_TEST_PORT": str(port),
        "PU_TEST_IMAGE_REPOSITORY": "pu-workspace-staging",
        "GMAIL_AUTO_SYNC_ENABLED": "false",
        "AI_SECRETARY_AUTOMATION_ENABLED": "false",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "GOOGLE_REDIRECT_URI": "",
        "TELEGRAM_BOT_TOKEN": "",
        "GEMINI_API_KEY": "",
        "YANDEX_CLIENT_ID": "",
        "YANDEX_CLIENT_SECRET": "",
    })
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.staging.", suffix=".tmp", dir=target.parent)
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
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    render(args.source, args.output, args.revision, args.port)
    print("Private staging runtime environment prepared; values were not logged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
