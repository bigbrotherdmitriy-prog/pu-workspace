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
import os
import re
import signal
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
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
BODY_LIMIT = 1024 * 1024
HEARTBEAT_CHECKS = {"durable_workers", "durable_scheduler"}
SAFE_CHECK_NAMES = HEARTBEAT_CHECKS | {
    "app_secret", "bootstrap_token", "token_encryption", "google_oauth", "telegram",
    "gmail_automation", "ai_secretary_automation", "local_ocr", "database", "schema", "dead_letter_queue",
}


class GateFailure(ValueError):
    """A content-free, stable diagnostic; never quote arbitrary remote bodies."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_check(name: str, check: dict) -> dict:
    result = {key: check.get(key) if type(check.get(key)) is bool else None for key in ("ok", "required")}
    message = check.get("message")
    patterns = {"durable_workers": r"active workers: [0-9]{1,6}; required: 2",
                "durable_scheduler": r"active schedulers: [0-9]{1,6}; required: 1",
                "schema": r"[0-9a-f]{8,40}"}
    if isinstance(message, str) and name in patterns and re.fullmatch(patterns[name], message):
        result["message"] = message
    return result


def safe_api_body(raw: bytes) -> dict:
    """Keep bounded-body evidence, but neither HTML nor arbitrary JSON secrets."""
    truncated = len(raw) > BODY_LIMIT
    bounded = raw[:BODY_LIMIT]
    result = {"bytes_read": len(raw), "truncated": truncated,
              "sha256": hashlib.sha256(bounded).hexdigest(), "parsed": None}
    try:
        body = json.loads(bounded)
    except (ValueError, UnicodeError):
        result["format"] = "non_json"
        return result
    result["format"] = "json"
    if not isinstance(body, dict):
        return result
    parsed = {}
    if type(body.get("ready")) is bool:
        parsed["ready"] = body["ready"]
    if body.get("status") == "ok":
        parsed["status"] = "ok"
    if isinstance(body.get("release"), str) and REVISION.fullmatch(body["release"]):
        parsed["release"] = body["release"]
    if isinstance(body.get("checks"), dict):
        parsed["checks"] = {
            name: safe_check(name, check)
            for name, check in body["checks"].items()
            if name in SAFE_CHECK_NAMES
            and isinstance(check, dict)
        }
    result["parsed"] = parsed
    return result


def remaining_timeout(deadline: float | None) -> float:
    remaining = 30 if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise GateFailure("readiness_wait_timeout", "public readiness wall-clock wait exhausted")
    return min(30, remaining)


def read_bounded_body(response, deadline: float | None = None) -> bytes:
    chunks, size = [], 0
    while size <= BODY_LIMIT:
        timeout = remaining_timeout(deadline)
        # urllib's socket timeout applies to individual I/O, not the whole
        # response. Refresh it while streaming so slow bodies cannot extend the
        # startup deadline. In-memory test responses have no underlying socket.
        stream = response
        for _ in range(3):
            stream = getattr(stream, "fp", stream)
            sock = getattr(getattr(stream, "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(timeout)
                break
        read = getattr(response, "read1", response.read)
        chunk = read(min(65536, BODY_LIMIT + 1 - size))
        remaining_timeout(deadline)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def read_api(client, url: str, evidence: dict, *, deadline: float | None = None) -> dict:
    evidence.update({"endpoint": urlsplit(url).path, "started_at": utc_now(),
                     "http_status": None, "body": None, "error_type": None})
    started = time.monotonic()
    try:
        with client.open(Request(url, headers={"Cache-Control": "no-cache"}), timeout=remaining_timeout(deadline)) as response:
            evidence["http_status"] = response.status
            if response.status != 200 or response.geturl() != url:
                evidence["body"] = safe_api_body(read_bounded_body(response, deadline))
                raise GateFailure("api_http_rejected", "public API requires HTTP 200 without redirects")
            raw = read_bounded_body(response, deadline)
            evidence["body"] = safe_api_body(raw)
            if len(raw) > BODY_LIMIT:
                raise GateFailure("api_body_too_large", "public API JSON exceeds the bounded body limit")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise GateFailure("api_json_invalid", "public API JSON is malformed") from exc
            if not isinstance(body, dict):
                raise GateFailure("api_schema_invalid", "public API JSON must be an object")
            return body
    except HTTPError as exc:
        evidence["http_status"] = exc.code
        evidence["error_type"] = type(exc).__name__
        try:
            evidence["body"] = safe_api_body(read_bounded_body(exc, deadline))
        except Exception as body_error:
            evidence["body_error_type"] = type(body_error).__name__
        finally:
            exc.close()
        raise GateFailure("api_http_rejected", "public API returned a non-200 HTTP status") from exc
    except Exception as exc:
        evidence["error_type"] = type(exc).__name__
        if hasattr(exc, "http_status"):
            evidence["http_status"] = exc.http_status
        raise
    finally:
        evidence["finished_at"] = utc_now()
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 6)


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def image_identity(image: str, expected_id: str, revision: str) -> dict:
    if not IMAGE_ID.fullmatch(expected_id) or not REVISION.fullmatch(revision):
        raise GateFailure("image_identity_invalid", "expected image ID and full release SHA are required")
    details = json.loads(docker("image", "inspect", image))[0]
    if details["Id"] != expected_id:
        raise GateFailure("image_identity_mismatch", "image identity does not match the accepted image ID")
    labels = details.get("Config", {}).get("Labels") or {}
    values = [labels[key] for key in ("com.pu-workspace.primary.revision", "org.opencontainers.image.revision") if key in labels]
    if not values or any(value != revision for value in values):
        raise GateFailure("image_revision_mismatch", "image revision label is missing or inconsistent")
    return {"image_id": expected_id, "revision": revision, "repo_digests": details.get("RepoDigests") or []}


def verify_components(project: str, image_id: str, revision: str, *, diagnostics: dict | None = None) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,47}", project):
        raise GateFailure("components_contract_invalid", "invalid Compose project")
    identifiers = docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}").splitlines()
    if not identifiers:
        raise GateFailure("components_missing", "Compose project has no containers")
    observed = {"backend": [], "worker": [], "scheduler": []}
    if diagnostics is not None:
        diagnostics.update({"observed": observed, "containers": []})
    for detail in json.loads(docker("inspect", *identifiers)):
        labels = detail["Config"].get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if service not in observed:
            continue
        env = dict(item.split("=", 1) for item in detail["Config"].get("Env", []) if "=" in item)
        if diagnostics is not None:
            diagnostics["containers"].append({
                "service": service, "running": detail["State"]["Running"],
                "image_matches": detail["Image"] == image_id,
                "revision_matches": env.get("PU_RELEASE_REVISION") == revision,
                "gmail_auto_sync_enabled": env.get("GMAIL_AUTO_SYNC_ENABLED", "").lower() == "true",
            })
        if not detail["State"]["Running"] or detail["Image"] != image_id or env.get("PU_RELEASE_REVISION") != revision:
            raise GateFailure("component_identity_mismatch", f"{service} is not running on the exact release image/SHA")
        if env.get("GMAIL_AUTO_SYNC_ENABLED", "").lower() != "true":
            raise GateFailure("gmail_flag_changed", f"{service}: GMAIL_AUTO_SYNC_ENABLED must remain true")
        observed[service].append(detail["Id"])
    if {key: len(value) for key, value in observed.items()} != {"backend": 1, "worker": 2, "scheduler": 1}:
        raise GateFailure("component_count_mismatch", "exactly one backend, two durable workers and one scheduler are required")
    return observed


def verify_readiness(base: str, revision: str, *, attempts: int = 25, interval: float = 5,
                     wait_timeout: float = 180,
                     diagnostics: list | None = None, opener=None) -> dict:
    """Wait only for proven startup heartbeat lag, never for other failed gates.

    At most 25 attempts / 120 seconds of pauses and a 180-second wall deadline.
    Each HTTP request keeps its original maximum 30-second timeout, shortened
    only when less startup-deadline time remains. Late readiness is rejected.
    HTTP/TLS/auth, wrong SHA, malformed responses and non-heartbeat failures
    stop immediately. No static bytes are fetched here or retried afterwards.
    """
    base = validate_base(base)
    if (type(attempts) is not int or not 1 <= attempts <= 25 or not 0 <= interval <= 5
            or not 0 < wait_timeout <= 180):
        raise GateFailure("readiness_wait_invalid", "readiness attempts must be 1..25, interval 0..5 and timeout >0..180 seconds")
    client = opener or build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    deadline = time.monotonic() + wait_timeout
    for number in range(1, attempts + 1):
        attempt = {"attempt": number, "started_at": utc_now(), "requests": [], "failed_required": []}
        if diagnostics is not None:
            diagnostics.append(attempt)
        started = time.monotonic()
        try:
            status_evidence = {}
            attempt["requests"].append(status_evidence)
            status = read_api(client, base + "/api/status", status_evidence, deadline=deadline)
            if status.get("status") != "ok" or status.get("release") != revision:
                raise GateFailure("release_mismatch", "public backend release is stale")
            readiness_evidence = {}
            attempt["requests"].append(readiness_evidence)
            readiness = read_api(client, base + "/api/readiness", readiness_evidence, deadline=deadline)
            checks = readiness.get("checks")
            if (type(readiness.get("ready")) is not bool or not isinstance(checks, dict) or not checks
                    or any(not isinstance(check, dict) or type(check.get("required")) is not bool
                           or type(check.get("ok")) is not bool for check in checks.values())):
                raise GateFailure("readiness_schema_invalid", "public readiness required checks are missing or malformed")
            failed = {name for name, check in checks.items() if check["required"] and check["ok"] is not True}
            attempt["failed_required"] = sorted(failed & SAFE_CHECK_NAMES)
            attempt["unknown_failed_required_count"] = len(failed - SAFE_CHECK_NAMES)
            remaining_timeout(deadline)
            if readiness["ready"] is True and not failed:
                attempt["result"] = "ready"
                return {"api/status": status, "api/readiness": readiness}
            if readiness["ready"] is not False or not failed or not failed <= HEARTBEAT_CHECKS:
                raise GateFailure("readiness_required_failed", "public readiness has a non-startup required failure")
            attempt["result"] = "waiting_for_heartbeats"
            if number == attempts:
                raise GateFailure("readiness_wait_exhausted", "public readiness heartbeat wait exhausted; not ready:true")
        except Exception as exc:
            attempt["error_type"] = type(exc).__name__
            attempt["reason_code"] = getattr(exc, "code", "api_transport_or_validation_failed")
            raise
        finally:
            attempt["finished_at"] = utc_now()
            attempt["elapsed_seconds"] = round(time.monotonic() - started, 6)
        time.sleep(min(interval, remaining_timeout(deadline)))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        failure = GateFailure("redirect_rejected", f"redirect refused ({code})")
        failure.http_status = code
        raise failure


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


def verify_http(files: list[dict], base: str, *, loopback: bool = False, opener=None,
                diagnostics: dict | None = None) -> list[dict]:
    base = validate_base(base, loopback)
    client = opener or build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    verified = []
    if diagnostics is not None:
        diagnostics.update({"state": "partial", "files_expected": len(files), "files_checked": 0, "files": []})
    for entry in files:
        path = entry["path"]
        url = base + "/new/" + ("" if path == "index.html" else quote(path, safe="/"))
        request = Request(url, headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache", "User-Agent": "PU-Static-Integrity/1"})
        evidence = {"path": path, "expected_size": entry["size"], "expected_sha256": entry["sha256"],
                    "status": None, "actual_size": 0, "actual_sha256": None, "complete_body": False,
                    "partial_sha256": None, "started_at": utc_now(), "verified": False}
        if diagnostics is not None:
            diagnostics["files"].append(evidence)
        started = time.monotonic()
        try:
            with client.open(request, timeout=30) as response:
                evidence["status"] = response.status
                if response.status != 200 or response.geturl() != url:
                    raise GateFailure("static_http_rejected", f"HTTP 200 without redirects required: {path}")
                encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
                if encoding not in ("", "identity"):
                    raise GateFailure("static_encoding_rejected", f"unexpected content encoding: {path}")
                media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                accepted = expected_content_types(path)
                if not media_type or (accepted and media_type not in accepted) or (not accepted and media_type == "text/html"):
                    raise GateFailure("static_type_rejected", f"unexpected content type: {path}")
                digest = hashlib.sha256()
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    evidence["actual_size"] = size
                    digest.update(chunk)
                    evidence["partial_sha256"] = digest.hexdigest()
                    if size > entry["size"]:
                        raise GateFailure("static_size_mismatch", f"public file size exceeds manifest: {path}")
                evidence.update({"complete_body": True, "actual_sha256": digest.hexdigest()})
                if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise GateFailure("static_hash_or_size_mismatch", f"public SHA-256/size mismatch: {path}")
            evidence["verified"] = True
        except HTTPError as exc:
            evidence.update({"status": exc.code, "error_type": type(exc).__name__, "reason_code": "static_http_rejected"})
            exc.close()
            raise GateFailure("static_http_rejected", f"HTTP 200 required: {path}") from exc
        except Exception as exc:
            evidence.update({"error_type": type(exc).__name__,
                             "reason_code": getattr(exc, "code", "static_transport_failed")})
            if hasattr(exc, "http_status"):
                evidence["status"] = exc.http_status
            raise
        finally:
            evidence["finished_at"] = utc_now()
            evidence["elapsed_seconds"] = round(time.monotonic() - started, 6)
        verified.append({"path": path, "size": size, "sha256": digest.hexdigest(), "status": 200})
        if diagnostics is not None:
            diagnostics["files_checked"] = len(verified)
    if not verified or len(verified) != len(files):
        raise ValueError("incomplete static verification")
    if diagnostics is not None:
        diagnostics["state"] = "complete"
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


def write_receipt(output, result: dict) -> None:
    output.seek(0)
    output.truncate()
    json.dump(result, output, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    output.write("\n")
    output.flush()
    os.fsync(output.fileno())


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
    parser.add_argument("--readiness-attempts", type=int, default=25, help="1..25 attempts; only missing worker/scheduler heartbeats can be retried")
    parser.add_argument("--readiness-interval", type=float, default=5, help="0..5 seconds between permitted readiness attempts")
    parser.add_argument("--readiness-timeout", type=float, default=180, help="Startup wall deadline >0..180 seconds; API I/O timeout remains at most 30 seconds")
    parser.add_argument("--receipt", type=Path, required=True, help="New, private evidence file; an existing receipt is never overwritten")
    args = parser.parse_args(argv)
    started = time.monotonic()
    result = {
        "schema_version": 2, "verified": False, "stage": "receipt_reserved", "reason_code": "in_progress",
        "started_at": utc_now(), "finished_at": None, "elapsed_seconds": 0,
        "image_id": args.expected_image_id if IMAGE_ID.fullmatch(args.expected_image_id) else None,
        "revision": args.revision if REVISION.fullmatch(args.revision) else None,
        "archive_sha256": args.archive_sha256 if args.archive_sha256 and SHA256.fullmatch(args.archive_sha256) else None,
        "manifest_sha256": None, "manifest": None, "files_checked": 0,
        "components": None, "component_checks": [], "readiness": None, "readiness_attempts": [],
        "static": {"state": "not_started", "files_expected": None, "files_checked": 0, "files": []},
        "receipt": str(args.receipt), "error_type": None,
    }
    output = None
    previous_signals = {}
    code = 1
    try:
        # Reserve before ANY acceptance check. Existing success/failure evidence
        # cannot be clobbered, including via symlinks. 0600 is enforced on POSIX.
        descriptor = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        output = os.fdopen(descriptor, "w", encoding="utf-8")
        if os.name == "posix":
            os.fchmod(output.fileno(), 0o600)
        write_receipt(output, result)
        def interrupted(signum, _frame):
            result["termination_signal"] = signum
            raise KeyboardInterrupt()
        for name in ("SIGTERM", "SIGHUP"):
            signum = getattr(signal, name, None)
            if signum is not None:
                previous_signals[signum] = signal.getsignal(signum)
                signal.signal(signum, interrupted)
        result["stage"] = "contract"
        if (not 1 <= args.readiness_attempts <= 25 or not 0 <= args.readiness_interval <= 5
                or not 0 < args.readiness_timeout <= 180):
            raise GateFailure("readiness_wait_invalid", "readiness attempts must be 1..25, interval 0..5 and timeout >0..180 seconds")
        if args.archive_sha256 and not SHA256.fullmatch(args.archive_sha256):
            raise GateFailure("contract_invalid", "invalid source archive SHA-256")
        if args.base_url and not args.compose_project:
            raise GateFailure("contract_invalid", "public acceptance requires --compose-project and full readiness/component gates")
        if args.base_url and not args.legacy_image and not args.archive_sha256:
            raise GateFailure("contract_invalid", "new production acceptance requires --archive-sha256")
        if args.compose_project and not args.base_url:
            raise GateFailure("contract_invalid", "production component verification requires --base-url HTTPS")
        if args.base_url or args.loopback_url:
            result["base_url"] = validate_base(args.base_url or args.loopback_url, loopback=bool(args.loopback_url))
        result["stage"] = "image_identity"
        identity = image_identity(args.image, args.expected_image_id, args.revision)
        result.update(identity)
        if args.compose_project:
            result["stage"] = "components"
            component_evidence = {"phase": "before_readiness"}
            result["component_checks"].append(component_evidence)
            result["components"] = verify_components(args.compose_project, identity["image_id"], args.revision, diagnostics=component_evidence)
            result["stage"] = "readiness"
            readiness = verify_readiness(args.base_url, args.revision, attempts=args.readiness_attempts,
                                         interval=args.readiness_interval, wait_timeout=args.readiness_timeout,
                                         diagnostics=result["readiness_attempts"])
            result["readiness"] = {key: safe_api_body(json.dumps(body).encode())["parsed"] for key, body in readiness.items()}
            # Waiting must not make the initial exact-image component gate stale.
            result["stage"] = "components_after_readiness"
            component_evidence = {"phase": "after_readiness"}
            result["component_checks"].append(component_evidence)
            result["components"] = verify_components(args.compose_project, identity["image_id"], args.revision, diagnostics=component_evidence)
        with tempfile.TemporaryDirectory(prefix="pu-static-image-") as temporary:
            result["stage"] = "image_frontend"
            manifest, manifest_hash = inspect_frontend(identity["image_id"], args.revision, Path(temporary), legacy=args.legacy_image)
            result.update({"manifest_sha256": manifest_hash, "manifest": manifest})
            result["static"]["files_expected"] = len(manifest["files"])
            if args.compose_project:
                result["scope"] = "retained-artifact rollback acceptance" if args.legacy_image else "complete production acceptance"
            if args.base_url or args.loopback_url:
                result["stage"] = "static_http"
                result["http_files"] = verify_http(manifest["files"], result["base_url"], loopback=bool(args.loopback_url), diagnostics=result["static"])
            else:
                result["scope"] = "image-only; HTTPS verification still required after cutover"
            result["files_checked"] = len(manifest["files"])
        result.update({"verified": True, "stage": "complete", "reason_code": "verified"})
        code = 0
    except (Exception, KeyboardInterrupt) as exc:
        result.update({"verified": False, "reason_code": getattr(exc, "code", "verification_failed"),
                       "error_type": type(exc).__name__, "files_checked": result["static"]["files_checked"]})
        if isinstance(exc, KeyboardInterrupt):
            result["reason_code"], code = "interrupted", 130
        # Exception text from a proxy, Docker or a socket can contain secrets.
        detail = str(exc) if isinstance(exc, GateFailure) else type(exc).__name__
        print(f"static verification failed: {detail}", file=sys.stderr)
    finally:
        for signum, previous in previous_signals.items():
            signal.signal(signum, previous)
        result.update({"finished_at": utc_now(), "elapsed_seconds": round(time.monotonic() - started, 6)})
        if output is not None:
            try:
                write_receipt(output, result)
            except Exception as exc:
                result.update({"verified": False, "reason_code": "receipt_save_failed", "error_type": type(exc).__name__})
                code = 1
                print("static verification failed: receipt could not be persisted", file=sys.stderr)
                try:
                    write_receipt(output, result)
                except Exception:
                    pass  # Disk/permission failures must never yield exit 0.
            finally:
                output.close()
        summary = {key: result[key] for key in (
            "verified", "stage", "reason_code", "error_type", "image_id", "revision",
            "files_checked", "manifest_sha256", "receipt", "elapsed_seconds",
        )}
        summary["static_state"] = result["static"]["state"]
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
