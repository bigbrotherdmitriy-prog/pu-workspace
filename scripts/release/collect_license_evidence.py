#!/usr/bin/env python3
"""Collect public package-registry license declarations for a pinned release.

The result is evidence, not a legal conclusion.  Invalid, missing or ambiguous
registry declarations remain unresolved instead of being guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PYPI_CLASSIFIERS = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
}
SPDX_LICENSE_IDS = {
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC-BY-4.0",
    "ISC",
    "LGPL-3.0-only",
    "MIT",
    "MIT-0",
    "MIT-CMU",
    "PSF-2.0",
}
SPDX_EXPRESSION = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.+-]*(?: WITH [A-Za-z0-9][A-Za-z0-9.+-]*)?"
    r"(?: (?:AND|OR) [A-Za-z0-9][A-Za-z0-9.+-]*(?: WITH [A-Za-z0-9][A-Za-z0-9.+-]*)?)*$"
)


def sha256_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def normalized_spdx(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("type")
    if not isinstance(value, str):
        return None
    expression = value.strip().removeprefix("(").removesuffix(")").strip()
    if not expression or expression.upper().startswith(("SEE LICENSE", "UNLICENSED", "UNKNOWN")):
        return None
    if not SPDX_EXPRESSION.fullmatch(expression):
        return None
    terms = re.split(r" (?:AND|OR) ", expression)
    if any(" WITH " in term or term not in SPDX_LICENSE_IDS for term in terms):
        return None
    return expression


def npm_record(name: str, version: str, metadata: dict[str, Any], url: str) -> dict[str, Any]:
    raw = metadata.get("license") or metadata.get("licenses")
    expression = normalized_spdx(raw)
    return {
        "purl": f"pkg:npm/{name.replace('@', '%40')}@{version}",
        "name": name,
        "version": version,
        "ecosystem": "npm",
        "license_declared": expression,
        "raw_license": raw if isinstance(raw, (str, dict, list)) else None,
        "status": "resolved-registry-declaration" if expression else "unresolved",
        "unresolved_reason": None if expression else "registry metadata has no valid SPDX license expression",
        "evidence_url": url,
        "metadata_sha256": sha256_json(metadata),
    }


def pypi_record(name: str, version: str, metadata: dict[str, Any], url: str) -> dict[str, Any]:
    info = metadata.get("info") if isinstance(metadata.get("info"), dict) else {}
    expression = normalized_spdx(info.get("license_expression"))
    evidence_kind = "pypi-license-expression"
    if not expression:
        expression = normalized_spdx(info.get("license"))
        evidence_kind = "pypi-license-field"
    if not expression:
        mapped = sorted({PYPI_CLASSIFIERS[row] for row in info.get("classifiers", []) if row in PYPI_CLASSIFIERS})
        if mapped:
            expression = " OR ".join(mapped)
            evidence_kind = "pypi-license-classifier"
    return {
        "purl": f"pkg:pypi/{name.lower()}@{version}",
        "name": name,
        "version": version,
        "ecosystem": "pypi",
        "license_declared": expression,
        "raw_license": info.get("license_expression") or info.get("license") or None,
        "status": "resolved-registry-declaration" if expression else "unresolved",
        "unresolved_reason": None if expression else "PyPI metadata has no usable license expression/classifier",
        "evidence_kind": evidence_kind if expression else None,
        "evidence_url": url,
        "metadata_sha256": sha256_json(metadata),
    }


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    value: object | None = None
    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(url, headers={"User-Agent": "PU-Workspace-license-evidence/1"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                value = json.load(response)
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (2**attempt))
    if value is None:
        assert last_error is not None
        raise last_error
    if not isinstance(value, dict):
        raise ValueError("registry response is not an object")
    return value


def load_release_module(root: Path):
    path = root / "scripts" / "legal_release_kit.py"
    spec = importlib.util.spec_from_file_location("legal_release_kit", path)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load release kit")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect(root: Path, ref: str, as_of: str, timeout: float) -> dict[str, Any]:
    module = load_release_module(root)
    requirements_blob = module.git_blob(root, ref, "backend/requirements.txt")
    locked_backend = module.load_validated_python_lock(root, ref, requirements_blob)
    backend = locked_backend if locked_backend is not None else module.parse_requirements(requirements_blob.decode())
    frontend = module.parse_pnpm_lock(module.git_blob(root, ref, "frontend/pnpm-lock.yaml").decode())
    records: list[dict[str, Any]] = []
    for component in backend:
        name, version = component["name"], component["version"]
        url = f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json"
        try:
            records.append(pypi_record(name, version, fetch_json(url, timeout), url))
        except Exception as exc:  # error class only; no response body or credentials
            records.append({
                **component,
                "ecosystem": "pypi",
                "license_declared": None,
                "status": "unresolved",
                "unresolved_reason": f"metadata fetch failed: {type(exc).__name__}",
                "evidence_url": url,
                "metadata_sha256": None,
            })
    for component in frontend:
        name, version = component["name"], component["version"]
        encoded_name = urllib.parse.quote(name, safe="")
        url = f"https://registry.npmjs.org/{encoded_name}/{urllib.parse.quote(version, safe='')}"
        try:
            records.append(npm_record(name, version, fetch_json(url, timeout), url))
        except Exception as exc:
            records.append({
                **component,
                "ecosystem": "npm",
                "license_declared": None,
                "status": "unresolved",
                "unresolved_reason": f"metadata fetch failed: {type(exc).__name__}",
                "evidence_url": url,
                "metadata_sha256": None,
            })
    compose_blob = module.git_blob(root, ref, "docker-compose.yml")
    dockerfile_blob = module.git_blob(root, ref, "backend/Dockerfile")
    compose = compose_blob.decode("utf-8")
    dockerfile = dockerfile_blob.decode("utf-8")
    for image in sorted(set(re.findall(r"^\s*(?:image:|FROM)\s+([^\s]+)", compose + "\n" + dockerfile, re.MULTILINE))):
        records.append({
            "purl": f"pkg:docker/{image}",
            "name": image,
            "version": "tag-not-digest-pinned",
            "ecosystem": "container",
            "license_declared": None,
            "status": "unresolved",
            "unresolved_reason": "container image is not digest-pinned; exact layer inventory and license texts are not reproducible",
            "evidence_url": None,
            "metadata_sha256": None,
        })
    for block in re.findall(r"apt-get\s+install\s+-y\s+--no-install-recommends\s+(.+?)\\", dockerfile, re.DOTALL):
        for package in block.replace("\\", " ").split():
            records.append({
                "purl": f"pkg:deb/debian/{package}",
                "name": package,
                "version": "resolved-at-image-build",
                "ecosystem": "container",
                "license_declared": None,
                "status": "unresolved",
                "unresolved_reason": "apt package version is resolved at image build and is not pinned",
                "evidence_url": None,
                "metadata_sha256": None,
            })
    records.sort(key=lambda row: row["purl"].casefold())
    return {
        "schema": "pu-workspace-license-evidence/v1",
        "release_ref": ref,
        "evidence_as_of": as_of,
        "method": "exact-version public PyPI/npm registry metadata; backend uses Linux hash lock when present, otherwise direct requirements; container declarations remain manifest-only; no license inferred from package name",
        "legal_effect": "package-declared metadata evidence only; licenseConcluded remains NOASSERTION pending counsel",
        "components": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    value = collect(args.root.resolve(), args.ref, args.as_of, args.timeout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
