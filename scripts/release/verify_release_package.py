#!/usr/bin/env python3
"""Fail-closed verifier for a PU Workspace commercial source archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_FILES = 10_000
ROOT = re.compile(r"^pu-workspace-[0-9a-f]{12}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SENSITIVE_ENV = re.compile(r"(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY)")
PLACEHOLDER = re.compile(r"^(?:|replace-with-[a-z0-9-]+|generate-with-[a-z0-9-]+)$")
_CLIENT_MARKERS = ("Налог" + "-Сервис", "ДИС" + "ИАЙ", "Бу" + "лат", "ГК" + "-08", "Б-" + "УЗП")
PII_PATTERNS = {
    "known client marker": re.compile("|".join(re.escape(value) for value in _CLIENT_MARKERS), re.IGNORECASE),
    "taxpayer identifier": re.compile(r"\bИНН\s*[:=]?\s*\d{10,12}\b", re.IGNORECASE),
    "SNILS": re.compile(r"\b\d{3}-\d{3}-\d{3}\s+\d{2}\b"),
}
SECRET_PATTERNS = {
    "private key": re.compile(r"BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY"),
    "OpenAI-like key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Telegram token": re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{30,}\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}


class PackageError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_member(member: tarfile.TarInfo, root: str) -> str:
    path = PurePosixPath(member.name)
    if member.name != path.as_posix() or path.is_absolute() or ".." in path.parts:
        raise PackageError("unsafe archive path")
    if not member.isfile():
        raise PackageError("archive contains a non-regular member")
    if not path.parts or path.parts[0] != root or len(path.parts) < 2:
        raise PackageError("archive has an unexpected root")
    if member.size < 0 or member.size > MAX_FILE_BYTES:
        raise PackageError("archive member exceeds size policy")
    return "/".join(path.parts[1:])


def validate_env_example(data: bytes) -> None:
    text = data.decode("utf-8")
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PackageError(f"invalid .env.example row {number}")
        key, value = line.split("=", 1)
        if key in seen or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise PackageError(f"invalid or duplicate environment key at row {number}")
        seen.add(key)
        if SENSITIVE_ENV.search(key) and not PLACEHOLDER.fullmatch(value):
            raise PackageError(f"sensitive .env.example value is not a placeholder: {key}")


def scan_text(path: str, data: bytes) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    findings = []
    for category, pattern in {**SECRET_PATTERNS, **PII_PATTERNS}.items():
        if category == "known client marker" and path == "scripts/legal_release_kit.py":
            continue
        if pattern.search(text):
            findings.append({"path": path, "category": category})
    for match in re.finditer(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s/:]+:([^\s/@]+)@", text, re.IGNORECASE):
        password = match.group(1)
        if not (
            password in {"password", "pu_change_me", "test", "testing"}
            or password.startswith("${")
            or password.startswith("replace-with-")
        ):
            findings.append({"path": path, "category": "credential-bearing DSN"})
    return findings


def verify(archive: Path) -> dict[str, object]:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PackageError("archive exceeds size policy")
    payloads: dict[str, bytes] = {}
    root: str | None = None
    with tarfile.open(archive, mode="r:gz") as source:
        members = source.getmembers()
        if len(members) > MAX_FILES:
            raise PackageError("archive contains too many files")
        for member in members:
            first = PurePosixPath(member.name).parts[0] if PurePosixPath(member.name).parts else ""
            if root is None:
                if not ROOT.fullmatch(first):
                    raise PackageError("archive root does not identify a commit")
                root = first
            if first != root:
                raise PackageError("archive contains multiple roots")
            relative = _safe_member(member, root)
            if relative in payloads:
                raise PackageError("archive contains a duplicate path")
            handle = source.extractfile(member)
            if handle is None:
                raise PackageError("cannot read archive member")
            payloads[relative] = handle.read(MAX_FILE_BYTES + 1)
    manifest_name = "docs/release/generated/RELEASE_MANIFEST.json"
    if manifest_name not in payloads or ".env.example" not in payloads:
        raise PackageError("archive lacks manifest or .env.example")
    manifest = json.loads(payloads[manifest_name].decode("utf-8"))
    if manifest.get("schema") != "pu-workspace-release-manifest/v2":
        raise PackageError("unsupported release manifest")
    commit = manifest.get("git_commit")
    if not isinstance(commit, str) or root != f"pu-workspace-{commit[:12]}":
        raise PackageError("archive root and manifest commit differ")
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise PackageError("manifest file list is missing")
    declared: dict[str, tuple[int, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PackageError("malformed manifest row")
        path, size, digest = row.get("path"), row.get("size"), row.get("sha256")
        if not isinstance(path, str) or not isinstance(size, int) or not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise PackageError("malformed manifest identity")
        if path in declared:
            raise PackageError("duplicate manifest path")
        declared[path] = (size, digest)
    actual_names = set(payloads) - {manifest_name}
    if actual_names != set(declared):
        raise PackageError("manifest and archive paths differ")
    for path, (size, digest) in declared.items():
        if len(payloads[path]) != size or sha256_bytes(payloads[path]) != digest:
            raise PackageError(f"manifest hash mismatch: {path}")
    validate_env_example(payloads[".env.example"])
    findings = [finding for path, data in payloads.items() for finding in scan_text(path, data)]
    if findings:
        categories = sorted({finding["category"] for finding in findings})
        raise PackageError("archive blocked by secret/PII categories: " + ", ".join(categories))
    return {
        "schema": "pu-workspace-release-verification/v1",
        "status": "PASS",
        "git_commit": commit,
        "archive_sha256": sha256_bytes(archive.read_bytes()),
        "file_count": len(payloads),
        "secret_pii_findings": 0,
        "topology": "single-root regular-files-only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        value = verify(args.archive)
    except (PackageError, OSError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"release package rejected: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
