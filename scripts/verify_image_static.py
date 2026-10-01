#!/usr/bin/env python3
"""Verify every public frontend byte against a pinned, never-started image.

No database, authentication, deployment or rollback actions are performed here.
Legacy extraction is explicit and is never a fallback for a new broken image.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

_MODULE_PATH = Path(__file__).resolve().parents[1] / "backend/scripts/frontend_manifest.py"
if not _MODULE_PATH.is_file():
    # check_public_smoke.py may be mounted into a runtime image, not a checkout.
    _MODULE_PATH = Path("/app/scripts/frontend_manifest.py")
_SPEC = importlib.util.spec_from_file_location("pu_frontend_manifest", _MODULE_PATH)
assert _SPEC and _SPEC.loader
manifest_tools = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = manifest_tools
_SPEC.loader.exec_module(manifest_tools)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def image_identity(image: str, expected_id: str, revision: str) -> dict:
    if not IMAGE_ID.fullmatch(expected_id) or not REVISION.fullmatch(revision):
        raise ValueError("expected image ID and full release SHA are required")
    details = json.loads(docker("image", "inspect", image))[0]
    if details["Id"] != expected_id:
        raise ValueError("image identity does not match the accepted image ID")
    labels = details.get("Config", {}).get("Labels") or {}
    values = [labels[key] for key in ("com.pu-workspace.primary.revision", "org.opencontainers.image.revision") if key in labels]
    if not values or any(value != revision for value in values):
        raise ValueError("image revision label is missing or inconsistent")
    return {"image_id": expected_id, "revision": revision, "repo_digests": details.get("RepoDigests") or []}


def verify_components(project: str, image_id: str, revision: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,47}", project):
        raise ValueError("invalid Compose project")
    identifiers = docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}").splitlines()
    if not identifiers:
        raise ValueError("Compose project has no containers")
    observed = {"backend": [], "worker": [], "scheduler": []}
    for detail in json.loads(docker("inspect", *identifiers)):
        labels = detail["Config"].get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if service not in observed:
            continue
        env = dict(item.split("=", 1) for item in detail["Config"].get("Env", []) if "=" in item)
        if not detail["State"]["Running"] or detail["Image"] != image_id or env.get("PU_RELEASE_REVISION") != revision:
            raise ValueError(f"{service} is not running on the exact release image/SHA")
        if env.get("GMAIL_AUTO_SYNC_ENABLED", "").lower() != "true":
            raise ValueError(f"{service}: GMAIL_AUTO_SYNC_ENABLED must remain true")
        observed[service].append(detail["Id"])
    if {key: len(value) for key, value in observed.items()} != {"backend": 1, "worker": 2, "scheduler": 1}:
        raise ValueError("exactly one backend, two durable workers and one scheduler are required")
    return observed


def verify_readiness(base: str, revision: str) -> dict:
    base = validate_base(base)
    client = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    result = {}
    for endpoint in ("api/status", "api/readiness"):
        url = base + "/" + endpoint
        with client.open(Request(url, headers={"Cache-Control": "no-cache"}), timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError(f"public {endpoint} is unavailable")
            result[endpoint] = json.loads(response.read(1024 * 1024))
    status, readiness = result["api/status"], result["api/readiness"]
    if status.get("status") != "ok" or status.get("release") != revision:
        raise ValueError("public backend release is stale")
    if readiness.get("ready") is not True:
        raise ValueError("public readiness is not ready:true")
    checks = readiness.get("checks")
    if not isinstance(checks, dict) or not checks:
        raise ValueError("public readiness required checks are missing")
    if any(check.get("required") and check.get("ok") is not True for check in checks.values()):
        raise ValueError("a required readiness check failed")
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError(f"redirect refused ({code}) for {req.full_url}")


def validate_base(base: str, loopback: bool = False) -> str:
    parsed = urlsplit(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("base URL must be a bare origin without credentials/query/path")
    if loopback:
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise ValueError("loopback HTTP is restricted to an explicit 127.0.0.1 port")
    elif parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("public verification requires HTTPS with certificate validation")
    return base.rstrip("/")


def expected_content_types(path: str) -> set[str] | None:
    suffix = Path(path).suffix.lower()
    return {
        ".html": {"text/html"}, ".js": {"text/javascript", "application/javascript"},
        ".mjs": {"text/javascript", "application/javascript"}, ".css": {"text/css"},
        ".json": {"application/json"}, ".webmanifest": {"application/manifest+json", "application/json"},
        ".svg": {"image/svg+xml"}, ".png": {"image/png"}, ".jpg": {"image/jpeg"},
        ".jpeg": {"image/jpeg"}, ".webp": {"image/webp"}, ".gif": {"image/gif"},
        ".ico": {"image/x-icon", "image/vnd.microsoft.icon"}, ".mp4": {"video/mp4"},
        ".webm": {"video/webm"}, ".woff": {"font/woff", "application/font-woff"},
        ".woff2": {"font/woff2"}, ".txt": {"text/plain"},
    }.get(suffix)


def verify_http(files: list[dict], base: str, *, loopback: bool = False, opener=None) -> list[dict]:
    base = validate_base(base, loopback)
    client = opener or build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    verified = []
    for entry in files:
        path = entry["path"]
        url = base + "/new/" + ("" if path == "index.html" else quote(path, safe="/"))
        request = Request(url, headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache", "User-Agent": "PU-Static-Integrity/1"})
        with client.open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError(f"HTTP 200 without redirects required: {path}")
            encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
            if encoding not in ("", "identity"):
                raise ValueError(f"unexpected content encoding: {path}")
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            accepted = expected_content_types(path)
            if not media_type or (accepted and media_type not in accepted) or (not accepted and media_type == "text/html"):
                raise ValueError(f"unexpected content type {media_type!r}: {path}")
            digest = hashlib.sha256()
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > entry["size"]:
                    raise ValueError(f"public file size exceeds manifest: {path}")
                digest.update(chunk)
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise ValueError(f"public SHA-256/size mismatch: {path}")
        verified.append({"path": path, "size": size, "sha256": digest.hexdigest(), "status": 200})
    if not verified or len(verified) != len(files):
        raise ValueError("incomplete static verification")
    return verified


def inspect_frontend(image_id: str, revision: str, directory: Path, *, legacy: bool = False) -> tuple[dict, str]:
    container = docker("create", "--network", "none", "--entrypoint", "/bin/true", image_id)
    if not re.fullmatch(r"[0-9a-f]{64}", container):
        raise ValueError("unexpected disposable container identity")
    try:
        root = directory / "react_dist"
        root.mkdir()
        docker("cp", f"{container}:/app/app/react_dist/.", str(root))
        manifest_path = directory / "frontend-build-manifest.json"
        copied = subprocess.run(["docker", "cp", f"{container}:/app/frontend-build-manifest.json", str(manifest_path)], capture_output=True, text=True)
        if copied.returncode:
            if not legacy or "Could not find the file" not in copied.stderr:
                raise ValueError("internal manifest unavailable (legacy mode must be explicit for an old image)")
            files = manifest_tools.scan_files(root)
            manifest_tools.validate_files(root, files)
            result = {"schema_version": 1, "revision": revision, "build_mode": "legacy", "files": files}
            raw = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        else:
            raw = manifest_path.read_bytes()
            result = json.loads(raw)
            manifest_tools.validate_manifest(result, root, revision)
            if result["build_mode"] != "production":
                raise ValueError("a development image cannot be deployed")
        return result, hashlib.sha256(raw).hexdigest()
    finally:
        docker("rm", container)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--expected-image-id", required=True)
    parser.add_argument("--revision", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--base-url")
    group.add_argument("--loopback-url")
    parser.add_argument("--legacy-image", action="store_true", help="Only an explicitly retained pre-stage-1 rollback image")
    parser.add_argument("--archive-sha256")
    parser.add_argument("--compose-project", help="After production cutover: also require four exact-image components, unchanged Gmail flag and public readiness")
    parser.add_argument("--receipt", type=Path, required=True, help="New, private evidence file; an existing receipt is never overwritten")
    args = parser.parse_args(argv)
    try:
        if args.archive_sha256 and not SHA256.fullmatch(args.archive_sha256):
            raise ValueError("invalid source archive SHA-256")
        identity = image_identity(args.image, args.expected_image_id, args.revision)
        if args.base_url and not args.compose_project:
            raise ValueError("public acceptance requires --compose-project and full readiness/component gates")
        if args.base_url and not args.legacy_image and not args.archive_sha256:
            raise ValueError("new production acceptance requires --archive-sha256")
        if args.compose_project and not args.base_url:
            raise ValueError("production component verification requires --base-url HTTPS")
        components = verify_components(args.compose_project, identity["image_id"], args.revision) if args.compose_project else None
        readiness = verify_readiness(args.base_url, args.revision) if args.compose_project else None
        with tempfile.TemporaryDirectory(prefix="pu-static-image-") as temporary:
            manifest, manifest_hash = inspect_frontend(identity["image_id"], args.revision, Path(temporary), legacy=args.legacy_image)
            result = {**identity, "manifest_sha256": manifest_hash, "manifest": manifest,
                      "archive_sha256": args.archive_sha256, "files_checked": len(manifest["files"]), "verified": True}
            if components is not None:
                result["components"] = components
                result["readiness"] = readiness
                result["scope"] = "retained-artifact rollback acceptance" if args.legacy_image else "complete production acceptance"
            if args.base_url or args.loopback_url:
                result["http_files"] = verify_http(manifest["files"], args.base_url or args.loopback_url, loopback=bool(args.loopback_url))
                result["base_url"] = args.base_url or args.loopback_url
            else:
                result["scope"] = "image-only; HTTPS verification still required after cutover"
            with args.receipt.open("x", encoding="utf-8") as output:
                json.dump(result, output, ensure_ascii=False, sort_keys=True, indent=2)
                output.write("\n")
        print(json.dumps({"verified": True, "image_id": identity["image_id"], "revision": args.revision,
                          "files_checked": result["files_checked"], "manifest_sha256": manifest_hash, "receipt": str(args.receipt)}))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"static verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
