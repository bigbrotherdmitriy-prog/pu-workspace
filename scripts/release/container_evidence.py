#!/usr/bin/env python3
"""Create and validate sanitized evidence for an exact built container image."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_REF = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")


class EvidenceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_dpkg(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        fields = raw.split("\t")
        if len(fields) != 3:
            raise EvidenceError(f"invalid dpkg row {number}")
        name, version, architecture = (field.strip() for field in fields)
        if not PACKAGE.fullmatch(name) or not version or not PACKAGE.fullmatch(architecture):
            raise EvidenceError(f"unsafe dpkg row {number}")
        key = (name, architecture)
        if key in seen:
            raise EvidenceError(f"duplicate package {name}:{architecture}")
        seen.add(key)
        rows.append({"name": name, "version": version, "architecture": architecture})
    if not rows:
        raise EvidenceError("empty dpkg inventory")
    return sorted(rows, key=lambda row: (row["name"], row["architecture"]))


def _spdx_summary(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("spdxVersion") != "SPDX-2.3" or not isinstance(value.get("packages"), list):
        raise EvidenceError("layer SBOM is not SPDX 2.3 JSON")
    unresolved = 0
    strong: list[str] = []
    weak: list[str] = []
    for package in value["packages"]:
        if not isinstance(package, dict):
            raise EvidenceError("malformed SPDX package")
        license_value = str(package.get("licenseConcluded") or package.get("licenseDeclared") or "NOASSERTION")
        if license_value in {"NOASSERTION", "NONE"}:
            unresolved += 1
        if re.search(r"(?:^|\W)(?:AGPL|GPL|SSPL)-", license_value):
            strong.append(str(package.get("SPDXID", "unknown")))
        if "LGPL-" in license_value:
            weak.append(str(package.get("SPDXID", "unknown")))
    return {
        "package_count": len(value["packages"]),
        "unresolved_license_count": unresolved,
        "strong_copyleft_spdx_ids": sorted(strong),
        "weak_copyleft_spdx_ids": sorted(weak),
    }


def capture(image_ref: str, inspect_path: Path, dpkg_path: Path, sbom_path: Path, release_commit: str) -> dict[str, Any]:
    if not IMAGE_REF.fullmatch(image_ref):
        raise EvidenceError("image reference must be digest-pinned")
    if not re.fullmatch(r"[0-9a-f]{40}", release_commit):
        raise EvidenceError("release commit must be a full SHA-1")
    inspect = json.loads(inspect_path.read_text(encoding="utf-8"))
    if isinstance(inspect, list) and len(inspect) == 1:
        inspect = inspect[0]
    if not isinstance(inspect, dict):
        raise EvidenceError("invalid image inspect document")
    image_id = inspect.get("Id")
    repo_digests = inspect.get("RepoDigests")
    layers = (inspect.get("RootFS") or {}).get("Layers")
    if not isinstance(image_id, str) or not DIGEST.fullmatch(image_id):
        raise EvidenceError("invalid image ID")
    if not isinstance(repo_digests, list) or image_ref not in repo_digests:
        raise EvidenceError("requested digest is absent from RepoDigests")
    if not isinstance(layers, list) or not layers or any(not isinstance(row, str) or not DIGEST.fullmatch(row) for row in layers):
        raise EvidenceError("invalid layer inventory")
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    value = {
        "schema": "pu-workspace-container-evidence/v1",
        "release_commit": release_commit,
        "image_ref": image_ref,
        "image_id": image_id,
        "layer_digests": layers,
        "os_package_manager": "dpkg",
        "os_packages": parse_dpkg(dpkg_path.read_text(encoding="utf-8")),
        "layer_sbom": {
            "format": "SPDX-2.3-json",
            "filename": sbom_path.name,
            "sha256": sha256_file(sbom_path),
            **_spdx_summary(sbom),
        },
        "legal_effect": "technical inventory only; unresolved licenses require counsel",
    }
    validate(value)
    return value


def validate(value: dict[str, Any]) -> None:
    if value.get("schema") != "pu-workspace-container-evidence/v1":
        raise EvidenceError("unsupported evidence schema")
    if not IMAGE_REF.fullmatch(str(value.get("image_ref", ""))):
        raise EvidenceError("image is not digest-pinned")
    if not DIGEST.fullmatch(str(value.get("image_id", ""))):
        raise EvidenceError("invalid image ID")
    layers = value.get("layer_digests")
    if not isinstance(layers, list) or not layers or len(layers) != len(set(layers)):
        raise EvidenceError("missing or duplicate layers")
    if any(not isinstance(row, str) or not DIGEST.fullmatch(row) for row in layers):
        raise EvidenceError("invalid layer digest")
    packages = value.get("os_packages")
    if not isinstance(packages, list) or not packages:
        raise EvidenceError("missing OS package inventory")
    keys = [(row.get("name"), row.get("architecture")) for row in packages if isinstance(row, dict)]
    if len(keys) != len(packages) or len(keys) != len(set(keys)):
        raise EvidenceError("malformed or duplicate OS packages")
    if any(not row.get("version") or not PACKAGE.fullmatch(str(row.get("name", ""))) for row in packages):
        raise EvidenceError("unversioned OS package")
    sbom = value.get("layer_sbom")
    if not isinstance(sbom, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(sbom.get("sha256", ""))):
        raise EvidenceError("missing layer SBOM digest")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("capture")
    make.add_argument("--image-ref", required=True)
    make.add_argument("--inspect", type=Path, required=True)
    make.add_argument("--dpkg", type=Path, required=True)
    make.add_argument("--sbom", type=Path, required=True)
    make.add_argument("--release-commit", required=True)
    make.add_argument("--out", type=Path, required=True)
    check = sub.add_parser("validate")
    check.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            value = capture(args.image_ref, args.inspect, args.dpkg, args.sbom, args.release_commit)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            validate(json.loads(args.evidence.read_text(encoding="utf-8")))
    except (EvidenceError, OSError, json.JSONDecodeError) as exc:
        print(f"container evidence rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
