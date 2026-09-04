#!/usr/bin/env python3
"""Reproducible, secret-safe PU Workspace commercial release kit builder.

The bundle is read from an immutable Git ref, never from the working tree.
It intentionally excludes tests and production/sales material because the
current repository contains client-like identifiers in test fixtures.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path


ALLOW_EXACT = {
    ".env.example", ".gitignore", "README.md", "LICENSE", "NOTICE",
    "docker-compose.yml", "backend/requirements.txt", "backend/alembic.ini",
    "backend/Dockerfile", "frontend/package.json", "frontend/pnpm-lock.yaml",
    "frontend/pnpm-workspace.yaml", "frontend/tsconfig.json",
    "frontend/vite.config.mjs", "frontend/vitest.config.ts",
}
ALLOW_PREFIXES = (
    "backend/app/", "backend/migrations/", "backend/scripts/",
    "frontend/src/", "frontend/public/", "docs/legal/", "docs/release/",
    "docs/architecture/", "docs/operations.md", "docs/retention-policy.md",
    "docs/USER_GUIDE_RU.md", "scripts/legal_release_kit.py",
    "scripts/check_release_package.py", "scripts/check_public_smoke.py",
)
FORBIDDEN_PATH = re.compile(
    r"(^|/)(?:\.env(?:\..+)?|\.git|node_modules|__pycache__|backups?|"
    r"server-access|\.ssh|dist|sales|tests?)(?:/|$)|"
    r"\.(?:pem|key|p12|pfx|jks|keystore|dump|sql|bak|backup)$",
    re.IGNORECASE,
)
SECRET_PATTERNS = {
    "private key": re.compile(r"BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY"),
    "OpenAI-like key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Telegram token": re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{30,}\b"),
}
CLIENT_MARKERS = re.compile(
    r"Налог-Сервис|ДИСИАЙ|Булат|ГК-08|Б-УЗП|\bИНН\s*\d{10,12}\b",
    re.IGNORECASE,
)
LICENSE_EVIDENCE_PATH = "docs/release/generated/license-evidence.json"


def run(root: Path, *args: str) -> bytes:
    return subprocess.check_output(args, cwd=root, stderr=subprocess.STDOUT)


def git_files(root: Path, ref: str) -> list[str]:
    output = run(root, "git", "ls-tree", "-r", "--name-only", ref).decode("utf-8")
    return sorted(line for line in output.splitlines() if line)


def allowed(path: str) -> bool:
    if path == ".env.example":
        return True
    if path.startswith("backend/app/react_dist/") or re.search(r"(?:^|/)[^/]+\.(?:test|spec)\.[^/]+$", path, re.IGNORECASE):
        return False
    return not FORBIDDEN_PATH.search(path) and (
        path in ALLOW_EXACT or any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in ALLOW_PREFIXES)
    )


def git_blob(root: Path, ref: str, path: str) -> bytes:
    return run(root, "git", "show", f"{ref}:{path}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scan_bytes(path: str, data: bytes) -> list[str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return [f"{path}: {name}" for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def parse_requirements(data: str) -> list[dict]:
    components = []
    for raw in data.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;]+)", line)
        if match:
            name, version = match.groups()
            components.append({"name": name, "version": version, "purl": f"pkg:pypi/{name.lower()}@{version}"})
    return components


def parse_pnpm_lock(data: str) -> list[dict]:
    components: dict[tuple[str, str], dict] = {}
    in_packages = False
    for line in data.splitlines():
        if line == "packages:":
            in_packages = True
            continue
        if not in_packages:
            continue
        # pnpm lockfiles contain other top-level mappings after ``packages``
        # (notably ``snapshots``).  Snapshot keys may look exactly like package
        # keys, but include peer-resolution suffixes and must not become SPDX
        # packages.  Stop at the first following top-level key.
        if line and not line.startswith((" ", "\t")):
            break
        match = re.match(r"^  ['\"]?([^\s].*?)@([^:'\"]+)['\"]?:\s*$", line)
        if not match:
            continue
        name, version = match.groups()
        version = version.split("(", 1)[0]
        key = (name, version)
        components[key] = {"name": name, "version": version, "purl": f"pkg:npm/{name.replace('@', '%40')}@{version}"}
    return list(components.values())


def spdx_document(name: str, namespace_suffix: str, components: list[dict]) -> dict:
    packages = []
    relationships = []
    for index, component in enumerate(sorted(components, key=lambda row: (row["name"].casefold(), row["version"]))):
        spdx_id = f"SPDXRef-Package-{index + 1}"
        evidence = component.get("license_evidence") or {}
        declared = component.get("license") or "NOASSERTION"
        package = {
            "SPDXID": spdx_id, "name": component["name"], "versionInfo": component["version"],
            "downloadLocation": "NOASSERTION", "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION", "licenseDeclared": declared,
            "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": component["purl"]}],
        }
        package["comment"] = (
            f"licenseConcluded is NOASSERTION pending legal review; status={evidence.get('status', 'unresolved')}; "
            f"reason={evidence.get('unresolved_reason') or 'package registry declaration recorded'}; "
            f"evidence={evidence.get('evidence_url') or 'manifest only'}"
        )
        packages.append(package)
        relationships.append({"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": spdx_id})
    return {
        "spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT",
        "name": name, "documentNamespace": f"https://pu-workspace.invalid/spdx/{namespace_suffix}",
        "creationInfo": {"created": "1970-01-01T00:00:00Z", "creators": ["Tool: scripts/legal_release_kit.py"]},
        "packages": packages, "relationships": relationships,
        "comment": "License fields marked NOASSERTION must be resolved and reviewed before sale.",
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_license_evidence(root: Path, ref: str, override: Path | None = None) -> dict[str, dict]:
    try:
        data = override.read_bytes() if override else git_blob(root, ref, LICENSE_EVIDENCE_PATH)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {}
    document = json.loads(data.decode("utf-8"))
    return {row["purl"]: row for row in document.get("components", []) if isinstance(row, dict) and row.get("purl")}


def attach_license_evidence(components: list[dict], evidence: dict[str, dict]) -> None:
    for component in components:
        record = evidence.get(component["purl"])
        if record and record.get("license_declared"):
            component["license"] = record["license_declared"]
        component["license_evidence"] = record or {
            "status": "unresolved",
            "unresolved_reason": "no exact-version license evidence record",
            "evidence_url": None,
        }


def write_license_bundle(out: Path, layers: dict[str, list[dict]]) -> list[Path]:
    rows = []
    for layer, components in layers.items():
        for component in components:
            evidence = component.get("license_evidence") or {}
            declared = component.get("license")
            rows.append({
                "layer": layer,
                "name": component["name"],
                "version": component["version"],
                "purl": component["purl"],
                "license_declared": declared,
                "license_concluded": None,
                "status": evidence.get("status", "unresolved"),
                "unresolved_reason": evidence.get("unresolved_reason"),
                "evidence_url": evidence.get("evidence_url"),
                "metadata_sha256": evidence.get("metadata_sha256"),
            })
    rows.sort(key=lambda row: (row["layer"], row["purl"].casefold()))
    strong = [row["purl"] for row in rows if re.search(r"(?:^|\W)(?:AGPL|SSPL|GPL)-", row.get("license_declared") or "")]
    weak = [row["purl"] for row in rows if "LGPL-" in (row.get("license_declared") or "")]
    unresolved = [row["purl"] for row in rows if not row.get("license_declared")]
    value = {
        "schema": "pu-workspace-third-party-license-matrix/v1",
        "components": rows,
        "summary": {
            "total": len(rows),
            "declared_license_evidenced": len(rows) - len(unresolved),
            "license_concluded": 0,
            "unresolved": len(unresolved),
            "strong_copyleft_declared": strong,
            "weak_copyleft_declared": weak,
            "scope_note": "backend direct requirements + complete pnpm packages + manifest-level container declarations",
        },
    }
    matrix = out / "third-party-license-matrix.json"
    write_json(matrix, value)
    notices = out / "THIRD_PARTY_NOTICES.md"
    lines = [
        "# Third-party license evidence bundle",
        "",
        "This file records exact component versions and package-declared license metadata. It is not a legal conclusion.",
        "`licenseConcluded` remains `NOASSERTION` until the right holder and counsel approve the release.",
        "",
        f"- Components: {len(rows)}",
        f"- Declared license evidenced: {len(rows) - len(unresolved)}",
        f"- Unresolved declarations: {len(unresolved)}",
        f"- Strong copyleft declared (GPL/AGPL/SSPL): {len(strong)}",
        f"- Weak copyleft declared (LGPL): {len(weak)}",
        "",
        "Machine-readable per-component evidence, URLs, hashes and unresolved reasons are in `third-party-license-matrix.json`.",
        "Package-specific LICENSE/NOTICE texts and container-layer licenses remain a release gate where the matrix says unresolved.",
        "",
    ]
    notices.write_text("\n".join(lines), encoding="utf-8")
    return [matrix, notices]


def generate_sbom(root: Path, out: Path, ref: str, evidence_override: Path | None = None) -> list[Path]:
    requirements_blob = git_blob(root, ref, "backend/requirements.txt")
    lock_blob = git_blob(root, ref, "frontend/pnpm-lock.yaml")
    compose_blob = git_blob(root, ref, "docker-compose.yml")
    dockerfile_blob = git_blob(root, ref, "backend/Dockerfile")
    backend = parse_requirements(requirements_blob.decode("utf-8"))
    frontend = parse_pnpm_lock(lock_blob.decode("utf-8"))
    license_evidence = load_license_evidence(root, ref, evidence_override)
    attach_license_evidence(backend, license_evidence)
    attach_license_evidence(frontend, license_evidence)
    compose = compose_blob.decode("utf-8")
    dockerfile = dockerfile_blob.decode("utf-8")
    images = sorted(set(re.findall(r"^\s*(?:image:|FROM)\s+([^\s]+)", compose + "\n" + dockerfile, re.MULTILINE)))
    system_packages = re.findall(r"apt-get\s+install\s+-y\s+--no-install-recommends\s+(.+?)\\", dockerfile, re.DOTALL)
    container_components = [{"name": image, "version": "tag-not-digest-pinned", "purl": f"pkg:docker/{image}"} for image in images]
    for block in system_packages:
        for package in block.replace("\\", " ").split():
            container_components.append({"name": package, "version": "resolved-at-image-build", "purl": f"pkg:deb/debian/{package}"})
    attach_license_evidence(container_components, license_evidence)
    paths = [out / "sbom-backend.spdx.json", out / "sbom-frontend.spdx.json", out / "sbom-containers.spdx.json"]
    write_json(paths[0], spdx_document("PU Workspace backend", f"{sha256(requirements_blob)}-backend", backend))
    write_json(paths[1], spdx_document("PU Workspace frontend", f"{sha256(lock_blob)}-frontend", frontend))
    write_json(paths[2], spdx_document("PU Workspace container declaration", f"{sha256(compose_blob + dockerfile_blob)}-containers", container_components))
    return paths + write_license_bundle(out, {"backend-direct": backend, "frontend-lock": frontend, "container-manifest": container_components})


def build_bundle(root: Path, out: Path, ref: str) -> tuple[Path, dict]:
    sha = run(root, "git", "rev-parse", ref).decode().strip()
    commit_time = int(run(root, "git", "show", "-s", "--format=%ct", sha).decode().strip())
    files = [path for path in git_files(root, sha) if allowed(path)]
    rejected = [path for path in git_files(root, sha) if not allowed(path)]
    entries = []
    payloads = []
    findings = []
    client_like_source_findings = []
    for path in files:
        data = git_blob(root, sha, path)
        findings.extend(scan_bytes(path, data))
        if (path.startswith("backend/app/") or path.startswith("frontend/src/")) and CLIENT_MARKERS.search(data.decode("utf-8", errors="ignore")):
            client_like_source_findings.append(path)
        entries.append({"path": path, "size": len(data), "sha256": sha256(data)})
        payloads.append((path, data))
    if findings:
        raise SystemExit("Bundle blocked by secret/client-data scan:\n" + "\n".join(findings))
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "pu-workspace-release-manifest/v1", "git_commit": sha,
        "git_ref_input": ref, "source_date_epoch": commit_time,
        "generated_at": datetime.fromtimestamp(commit_time, timezone.utc).isoformat(),
        "scope_policy": "explicit allowlist; tests, sales, production data and secrets excluded",
        "files": entries, "excluded_file_count": len(rejected),
        "client_like_source_identifiers_for_owner_review": client_like_source_findings,
        "legal_status": "DRAFT - requires owner data and Russian legal review",
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    payloads.append(("docs/release/generated/RELEASE_MANIFEST.json", manifest_bytes))
    archive = out / f"pu-workspace-{sha[:12]}-commercial-source.tar.gz"
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path, data in sorted(payloads):
            info = tarfile.TarInfo(f"pu-workspace-{sha[:12]}/{path}")
            info.size = len(data); info.mtime = commit_time; info.mode = 0o644; info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    with archive.open("wb") as target, gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as zipped:
        zipped.write(tar_buffer.getvalue())
    manifest_path = out / "RELEASE_MANIFEST.json"
    write_json(manifest_path, manifest)
    checksums = out / "SHA256SUMS"
    checksums.write_text(f"{sha256(archive.read_bytes())}  {archive.name}\n{sha256(manifest_path.read_bytes())}  {manifest_path.name}\n", encoding="ascii")
    return archive, manifest


def audit(root: Path, ref: str, bundle: Path | None = None) -> dict:
    tracked = git_files(root, ref)
    tracked_findings = []
    for path in tracked:
        if path.startswith("backend/tests/"):
            data = git_blob(root, ref, path)
            if CLIENT_MARKERS.search(data.decode("utf-8", errors="ignore")):
                tracked_findings.append(path)
    result = {
        "git_ref": ref, "git_commit": run(root, "git", "rev-parse", ref).decode().strip(),
        "tracked_paths_excluded_by_release_policy": [
            path for path in tracked
            if path != ".env.example" and FORBIDDEN_PATH.search(path) and not path.startswith("backend/tests/")
        ],
        "client_like_test_fixtures": tracked_findings,
        "bundle_present": bool(bundle and bundle.is_file()),
        "bundle_sha256": sha256(bundle.read_bytes()) if bundle and bundle.is_file() else None,
        "status": "PASS_WITH_DOCUMENTED_EXCLUSIONS" if tracked_findings else "PASS",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("sbom", "bundle", "audit", "all"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--out", type=Path, default=Path("dist/legal-release"))
    parser.add_argument("--license-evidence", type=Path)
    args = parser.parse_args()
    root, out = args.root.resolve(), args.out.resolve()
    bundle = None
    if args.command in {"sbom", "all"}:
        generate_sbom(root, out, args.ref, args.license_evidence.resolve() if args.license_evidence else None)
    if args.command in {"bundle", "all"}:
        bundle, _ = build_bundle(root, out, args.ref)
    if args.command in {"audit", "all"}:
        write_json(out / "AUDIT_RESULT.json", audit(root, args.ref, bundle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
