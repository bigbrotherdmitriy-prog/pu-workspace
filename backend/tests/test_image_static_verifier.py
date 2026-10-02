import hashlib
import importlib.util
import io
import json
import base64
import os
import sys
from urllib.error import HTTPError, URLError
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("image_static_verifier", ROOT / "scripts/verify_image_static.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
REVISION = "a" * 40
IMAGE = "sha256:" + "b" * 64


class Response(io.BytesIO):
    def __init__(self, body, url, *, status=200, content_type="text/javascript", encoding="identity"):
        super().__init__(body)
        self.url, self.status = url, status
        self.headers = {"Content-Type": content_type, "Content-Encoding": encoding}

    def geturl(self):
        return self.url


class Client:
    def __init__(self, payloads):
        self.payloads, self.calls = payloads, []

    def open(self, request, timeout):
        self.calls.append(request)
        body, options = self.payloads[request.full_url]
        return Response(body, options.pop("url", request.full_url), **options)


def entry(path, body):
    return {"path": path, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def test_checks_every_file_including_html_video_lazy_chunk_and_service_worker():
    bodies = {"index.html": (b"<div id='root'></div>", "text/html"),
              "assets/lazy.js": (b"export default 1", "text/javascript"),
              "video/model.mp4": (b"video-bytes", "video/mp4"),
              "service-worker.js": (b"self.foo=1", "application/javascript")}
    client = Client({"https://example.test/new/" + ("" if path == "index.html" else path):
                     (body, {"content_type": kind}) for path, (body, kind) in bodies.items()})
    results = verifier.verify_http([entry(path, body) for path, (body, _) in bodies.items()], "https://example.test/", opener=client)
    assert len(results) == len(bodies) == len(client.calls)
    assert all(request.get_header("Accept-encoding") == "identity" for request in client.calls)


@pytest.mark.parametrize("options,body", [({"status": 401}, b"JS"),
    ({"url": "https://elsewhere.test/new/assets/main.js"}, b"JS"),
    ({"encoding": "gzip"}, b"JS"), ({"content_type": "text/html"}, b"JS"),
    ({}, b"XX"), ({}, b"JSextra"), ({}, b"J")])
def test_http_fails_closed_on_auth_redirect_encoding_wrong_type_corruption_or_size(options, body):
    client = Client({"https://example.test/new/assets/main.js": (body, options)})
    with pytest.raises(ValueError):
        verifier.verify_http([entry("assets/main.js", b"JS")], "https://example.test", opener=client)


@pytest.mark.parametrize("url", ["http://example.test", "https://user:pass@example.test", "https://example.test/new/", "https://example.test?token=x"])
def test_public_url_requires_https_bare_origin(url):
    with pytest.raises(ValueError):
        verifier.validate_base(url)


def test_loopback_is_explicit_and_cannot_be_remote_http():
    assert verifier.validate_base("http://127.0.0.1:8000", True) == "http://127.0.0.1:8000"
    with pytest.raises(ValueError):
        verifier.validate_base("http://example.test:8000", True)
    with pytest.raises(ValueError):
        verifier.validate_base("http://127.0.0.1:8000")


def test_image_identity_never_trusts_tag_or_a_conflicting_revision_label():
    details = [{"Id": IMAGE, "Config": {"Labels": {"com.pu-workspace.primary.revision": REVISION}}, "RepoDigests": []}]
    with patch.object(verifier, "docker", return_value=json.dumps(details)):
        assert verifier.image_identity("mutable:tag", IMAGE, REVISION)["image_id"] == IMAGE
        with pytest.raises(ValueError, match="identity"):
            verifier.image_identity("mutable:tag", "sha256:" + "c" * 64, REVISION)
        details[0]["Config"]["Labels"]["org.opencontainers.image.revision"] = "d" * 40
    with patch.object(verifier, "docker", return_value=json.dumps(details)):
        with pytest.raises(ValueError, match="revision"):
            verifier.image_identity("mutable:tag", IMAGE, REVISION)


def test_production_components_require_four_exact_images_and_gmail_true():
    details = []
    for index, service in enumerate(("backend", "worker", "worker", "scheduler")):
        details.append({"Id": str(index), "Image": IMAGE, "State": {"Running": True},
                        "Config": {"Labels": {"com.docker.compose.service": service},
                                   "Env": ["PU_RELEASE_REVISION=" + REVISION, "GMAIL_AUTO_SYNC_ENABLED=true"]}})
    with patch.object(verifier, "docker", side_effect=lambda *args: "1\n2\n3\n4" if args[0] == "ps" else json.dumps(details)):
        assert len(verifier.verify_components("puw-primary-next", IMAGE, REVISION)["worker"]) == 2
        details[1]["Image"] = "sha256:" + "c" * 64
        with pytest.raises(ValueError, match="exact release"):
            verifier.verify_components("puw-primary-next", IMAGE, REVISION)
        details[1]["Image"] = IMAGE
        details[1]["Config"]["Env"][-1] = "GMAIL_AUTO_SYNC_ENABLED=false"
        with pytest.raises(ValueError, match="GMAIL"):
            verifier.verify_components("puw-primary-next", IMAGE, REVISION)


def test_deploy_requires_manifest_before_db_start_and_checks_legacy_rollback():
    source = (ROOT / "scripts/deploy-primary-first-host.sh").read_text()
    assert source.index("verify_image_static.py") < source.index("compose up -d db")
    assert 'CANDIDATE_IMAGE=$(docker image inspect' in source
    assert '--legacy-image' in source and 'static_smoke "$PREVIOUS_RELEASE" "$PREVIOUS_IMAGE" rollback' in source
    assert 'static_smoke "$RELEASE_DIR" "$CANDIDATE_IMAGE" candidate' in source


@pytest.mark.parametrize("legacy,has_manifest", [(False, True), (True, True), (True, False)])
def test_image_extraction_never_starts_entrypoint_and_supports_explicit_legacy(tmp_path, legacy, has_manifest):
    commands = []
    container = "f" * 64
    def docker(*args):
        commands.append(args)
        if args[0] == "create":
            assert args == ("create", "--network", "none", "--entrypoint", "/bin/true", IMAGE)
            return container
        if args[0] == "cp":
            root = Path(args[-1])
            (root / "index.html").write_text('<script src="/new/main.js"></script>')
            (root / "main.js").write_bytes(b"JS")
        return ""
    def copy_manifest(args, **kwargs):
        from types import SimpleNamespace
        if not has_manifest:
            return SimpleNamespace(returncode=1, stderr="Error response from daemon: Could not find the file /app/frontend-build-manifest.json")
        root = tmp_path / "react_dist"
        manifest = verifier.manifest_tools.create_manifest(root, REVISION,
            {"node_version": "22.20.0", "pnpm_version": "10.17.1", "lockfile_sha256": "a" * 64})
        Path(args[-1]).write_text(json.dumps(manifest))
        return SimpleNamespace(returncode=0, stderr="")
    with patch.object(verifier, "docker", side_effect=docker), patch.object(verifier.subprocess, "run", side_effect=copy_manifest):
        manifest, digest = verifier.inspect_frontend(IMAGE, REVISION, tmp_path, legacy=legacy)
    assert len(manifest["files"]) == 2
    assert manifest["build_mode"] == ("production" if has_manifest else "legacy")
    assert len(digest) == 64 and commands[-1] == ("rm", container)
    assert not any(command[0] in {"run", "start", "exec"} for command in commands)


def test_missing_manifest_is_not_automatic_legacy_fallback_and_container_is_cleaned(tmp_path):
    from types import SimpleNamespace
    container = "f" * 64
    def docker(*args):
        if args[0] == "create":
            return container
        if args[0] == "cp":
            (Path(args[-1]) / "index.html").write_text("<html/>")
        return ""
    with patch.object(verifier, "docker", side_effect=docker) as commands, patch.object(verifier.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr="Could not find the file")):
        with pytest.raises(ValueError, match="manifest unavailable"):
            verifier.inspect_frontend(IMAGE, REVISION, tmp_path)
    assert commands.call_args.args == ("rm", container)


def test_rollback_restores_full_artifact_and_never_downgrades_database():
    source = (ROOT / "scripts/rollback-primary-release.sh").read_text()
    assert 'flock -n' in source
    assert source.index('schema compatibility not proven') < source.index('mv -Tf')
    assert 'backend worker scheduler' in source
    assert '--legacy-image --compose-project' in source
    assert 'alembic downgrade' not in source
    assert '--expected-image-id "$PREVIOUS_IMAGE"' in source
    assert 'image: %s' in source
    assert 'pin_primary_image.py' in source


def test_public_acceptance_cannot_skip_components_or_archive(tmp_path, capsys):
    arguments = ["--image", IMAGE, "--expected-image-id", IMAGE, "--revision", REVISION,
                 "--base-url", "https://example.test", "--receipt", str(tmp_path / "receipt.json")]
    with patch.object(verifier, "image_identity", return_value={"image_id": IMAGE}):
        assert verifier.main(arguments) == 1
        assert "compose-project" in capsys.readouterr().err
        first = json.loads((tmp_path / "receipt.json").read_text())
        assert first["verified"] is False and first["static"]["state"] == "not_started"
        arguments[-1] = str(tmp_path / "receipt2.json")
        assert verifier.main(arguments + ["--compose-project", "puw-primary-next"]) == 1
        assert "archive-sha256" in capsys.readouterr().err
    second = json.loads((tmp_path / "receipt2.json").read_text())
    assert second["reason_code"] == "contract_invalid" and second["stage"] == "contract"


def readiness_body(*, ready=True, worker=True, scheduler=True, database=True):
    return {"ready": ready, "checks": {
        "database": {"required": True, "ok": database},
        "durable_workers": {"required": True, "ok": worker},
        "durable_scheduler": {"required": True, "ok": scheduler},
    }}


STATUS = {"status": "ok", "release": REVISION}


class SequenceClient:
    def __init__(self, actions):
        self.actions, self.calls = list(actions), []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        path, body, options = action
        assert request.full_url == "https://example.test" + path
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        return Response(body, request.full_url, **options)


def api_actions(body, status=STATUS):
    return [("/api/status", status, {}), ("/api/readiness", body, {})]


def test_readiness_only_heartbeat_lag_retries_and_records_each_http200():
    client = SequenceClient(api_actions(readiness_body(ready=False, worker=False)) + api_actions(readiness_body()))
    diagnostics = []
    with patch.object(verifier.time, "sleep") as sleep:
        result = verifier.verify_readiness("https://example.test", REVISION, attempts=2,
                                           diagnostics=diagnostics, opener=client)
    assert result["api/readiness"]["ready"] is True
    assert len(client.calls) == 4 and sleep.call_args.args == (5,)
    assert diagnostics[0]["failed_required"] == ["durable_workers"]
    assert diagnostics[0]["result"] == "waiting_for_heartbeats"
    assert diagnostics[1]["result"] == "ready"
    assert all(request["http_status"] == 200 and request["finished_at"] for attempt in diagnostics for request in attempt["requests"])
    assert all(0 < timeout <= 30 for _, timeout in client.calls)


def test_readiness_exhaustion_has_exact_attempt_bound_and_no_static_requests():
    body = readiness_body(ready=False, worker=False, scheduler=False)
    client = SequenceClient(api_actions(body) * 3)
    diagnostics = []
    with patch.object(verifier.time, "sleep") as sleep, pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness("https://example.test", REVISION, attempts=3, opener=client, diagnostics=diagnostics)
    assert error.value.code == "readiness_wait_exhausted"
    assert len(client.calls) == 6 and sleep.call_count == 2
    assert len(diagnostics) == 3 and all("/new/" not in url for url, _ in client.calls)


@pytest.mark.parametrize("body", [
    {"ready": False}, {"ready": False, "checks": {}},
    {"ready": False, "checks": {"durable_workers": "invalid"}},
    {"ready": False, "checks": {"durable_workers": {"required": True, "ok": "false"}}},
    readiness_body(ready=False), readiness_body(ready=False, worker=False, database=False),
    readiness_body(ready=True, worker=False), {"ready": "true", "checks": readiness_body()["checks"]},
])
def test_non_startup_or_malformed_readiness_fails_on_first_attempt(body):
    client = SequenceClient(api_actions(body))
    with patch.object(verifier.time, "sleep") as sleep, pytest.raises(verifier.GateFailure):
        verifier.verify_readiness("https://example.test", REVISION, opener=client)
    assert len(client.calls) == 2 and sleep.call_count == 0


def test_wrong_release_fails_before_readiness_and_without_retry():
    client = SequenceClient([("/api/status", {"status": "ok", "release": "c" * 40}, {})])
    with patch.object(verifier.time, "sleep") as sleep, pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness("https://example.test", REVISION, opener=client)
    assert error.value.code == "release_mismatch" and len(client.calls) == 1 and not sleep.called


@pytest.mark.parametrize("http_code", [401, 403, 500, 502, 503, 504])
def test_http_error_body_is_bounded_safe_and_never_retried(http_code):
    raw = json.dumps({"ready": False, "token": "DO_NOT_LEAK", "checks": readiness_body(worker=False)["checks"]}).encode()
    client = SequenceClient([HTTPError("https://example.test/api/status", http_code, "unsafe message", {}, io.BytesIO(raw))])
    diagnostics = []
    with patch.object(verifier.time, "sleep") as sleep, pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness("https://example.test", REVISION, opener=client, diagnostics=diagnostics)
    evidence = diagnostics[0]["requests"][0]
    assert error.value.code == "api_http_rejected" and evidence["http_status"] == http_code
    assert evidence["body"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert evidence["body"]["parsed"]["ready"] is False
    assert "DO_NOT_LEAK" not in json.dumps(diagnostics) and not sleep.called


@pytest.mark.parametrize("body", [b"<html>SECRET_PROXY_BODY</html>", b"SECRET" * (verifier.BODY_LIMIT // 6 + 10)], ids=["html", "oversized"])
def test_http_error_never_leaks_html_or_oversized_body(body):
    diagnostics = []
    client = SequenceClient([HTTPError("https://example.test/api/status", 503, "proxy", {}, io.BytesIO(body))])
    with pytest.raises(verifier.GateFailure):
        verifier.verify_readiness("https://example.test", REVISION, opener=client, diagnostics=diagnostics)
    evidence = diagnostics[0]["requests"][0]["body"]
    assert evidence["bytes_read"] <= verifier.BODY_LIMIT + 1
    assert evidence["truncated"] is (len(body) > verifier.BODY_LIMIT)
    assert "SECRET" not in json.dumps(diagnostics) and evidence["parsed"] is None


def test_transport_error_is_not_retried_or_recorded_as_readiness_http200():
    diagnostics = []
    client = SequenceClient([URLError("SECRET_NETWORK_ERROR")])
    with patch.object(verifier.time, "sleep") as sleep, pytest.raises(URLError):
        verifier.verify_readiness("https://example.test", REVISION, opener=client, diagnostics=diagnostics)
    assert diagnostics[0]["requests"][0]["http_status"] is None
    assert "SECRET" not in json.dumps(diagnostics) and not sleep.called


def main_arguments(receipt):
    return ["--image", IMAGE, "--expected-image-id", IMAGE, "--revision", REVISION,
            "--base-url", "https://example.test", "--compose-project", "puw-primary-next",
            "--archive-sha256", "c" * 64, "--receipt", str(receipt), "--readiness-interval", "0"]


def run_mocked_public(arguments, client, manifest, *, identity_error=None, components_error=None):
    with patch.object(verifier, "image_identity", side_effect=identity_error,
                      return_value={"image_id": IMAGE, "revision": REVISION, "repo_digests": []}), \
         patch.object(verifier, "verify_components", side_effect=components_error,
                      return_value={"backend": ["b"], "worker": ["w1", "w2"], "scheduler": ["s"]}) as components, \
         patch.object(verifier, "inspect_frontend", return_value=(manifest, "d" * 64)) as inspect, \
         patch.object(verifier, "build_opener", return_value=client):
        result = verifier.main(arguments)
    return result, components.call_count, inspect.call_count


def test_success_receipt_covers_all_bytes_after_ready_and_rechecks_components(tmp_path, capsys):
    receipt = tmp_path / "receipt.json"
    files = [entry("index.html", b"HTML"), entry("assets/main.js", b"JS")]
    body = readiness_body()
    body["token"] = "SUCCESS_SECRET"
    client = SequenceClient(api_actions(readiness_body(ready=False, worker=False)) + api_actions(body) + [
        ("/new/", b"HTML", {"content_type": "text/html"}), ("/new/assets/main.js", b"JS", {})])
    code, components, _ = run_mocked_public(main_arguments(receipt), client, {"files": files})
    saved = json.loads(receipt.read_text())
    assert code == 0 and saved["verified"] is True and components == 2
    assert saved["static"]["state"] == "complete" and saved["files_checked"] == saved["static"]["files_checked"] == 2
    assert [url for url, _ in client.calls][-2:] == ["https://example.test/new/", "https://example.test/new/assets/main.js"]
    assert len(saved["readiness_attempts"]) == 2 and saved["finished_at"]
    assert "SUCCESS_SECRET" not in receipt.read_text() + capsys.readouterr().out
    if os.name == "posix":
        assert receipt.stat().st_mode & 0o777 == 0o600


def test_actual_byte_mismatch_saves_partial_receipt_without_retry(tmp_path):
    receipt = tmp_path / "receipt.json"
    files = [entry("index.html", b"HTML"), entry("assets/main.js", b"JS")]
    client = SequenceClient(api_actions(readiness_body()) + [
        ("/new/", b"HTML", {"content_type": "text/html"}), ("/new/assets/main.js", b"XX", {})])
    code, _, _ = run_mocked_public(main_arguments(receipt), client, {"files": files})
    saved = json.loads(receipt.read_text())
    assert code == 1 and saved["verified"] is False and saved["stage"] == "static_http"
    assert saved["static"]["state"] == "partial" and saved["files_checked"] == 1
    failed = saved["static"]["files"][1]
    assert failed["complete_body"] is True and failed["actual_size"] == failed["expected_size"] == 2
    assert failed["actual_sha256"] == hashlib.sha256(b"XX").hexdigest() != failed["expected_sha256"]
    assert len(client.calls) == 4 and not client.actions


@pytest.mark.parametrize("stage", ["image", "components", "readiness", "manifest", "interrupt"])
def test_every_operational_failure_persists_private_unverified_receipt(tmp_path, stage, capsys):
    receipt = tmp_path / "receipt.json"
    client = SequenceClient([HTTPError("https://example.test/api/status", 403, "SECRET", {}, io.BytesIO(b"SECRET"))])
    identity_error = RuntimeError("SECRET") if stage == "image" else KeyboardInterrupt() if stage == "interrupt" else None
    components_error = ValueError("SECRET") if stage == "components" else None
    with patch.object(verifier, "image_identity", side_effect=identity_error, return_value={"image_id": IMAGE}), \
         patch.object(verifier, "verify_components", side_effect=components_error, return_value={}), \
         patch.object(verifier, "verify_readiness", side_effect=None if stage == "readiness" else lambda *args, **kwargs: {"api/status": STATUS, "api/readiness": readiness_body()}) if stage != "readiness" else patch.object(verifier, "build_opener", return_value=client), \
         patch.object(verifier, "inspect_frontend", side_effect=RuntimeError("SECRET")):
        code = verifier.main(main_arguments(receipt))
    saved = json.loads(receipt.read_text())
    assert code == (130 if stage == "interrupt" else 1)
    assert saved["verified"] is False and saved["static"]["state"] == "not_started"
    assert saved["finished_at"] and saved["error_type"]
    assert "SECRET" not in receipt.read_text() + capsys.readouterr().out


def test_existing_receipt_never_overwritten_or_checks_started(tmp_path):
    receipt = tmp_path / "receipt.json"
    previous = b'{"verified":true,"revision":"retained"}\n'
    receipt.write_bytes(previous)
    with patch.object(verifier, "image_identity") as identity:
        assert verifier.main(main_arguments(receipt)) == 1
    assert receipt.read_bytes() == previous and not identity.called


def test_redirect_preserves_http_code_and_never_follows_or_retries():
    class RedirectClient:
        def open(self, request, timeout):
            return verifier.NoRedirect().redirect_request(request, None, 302, "SECRET", {}, "https://evil.test/SECRET")
    diagnostics = []
    with patch.object(verifier.time, "sleep") as sleep, pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness("https://example.test", REVISION, opener=RedirectClient(), diagnostics=diagnostics)
    assert error.value.code == "redirect_rejected"
    assert diagnostics[0]["requests"][0]["http_status"] == 302 and not sleep.called
    assert "SECRET" not in json.dumps(diagnostics)


def test_safe_projection_retains_only_known_count_messages():
    body = readiness_body(ready=False, worker=False)
    body["checks"]["durable_workers"]["message"] = "active workers: 0; required: 2"
    body["checks"]["database"]["message"] = "SECRET_DATABASE_URL"
    body["checks"]["durable_scheduler"]["message"] = "SECRET"
    parsed = verifier.safe_api_body(json.dumps(body).encode())["parsed"]
    assert parsed["checks"]["durable_workers"]["message"] == "active workers: 0; required: 2"
    assert "SECRET" not in json.dumps(parsed)


def test_normal_termination_saves_failure_and_restores_signal_handler(tmp_path):
    receipt = tmp_path / "receipt.json"
    previous = verifier.signal.getsignal(verifier.signal.SIGTERM)
    def terminate(*args):
        verifier.signal.getsignal(verifier.signal.SIGTERM)(verifier.signal.SIGTERM, None)
    with patch.object(verifier, "image_identity", side_effect=terminate):
        assert verifier.main(main_arguments(receipt)) == 130
    saved = json.loads(receipt.read_text())
    assert saved["verified"] is False and saved["reason_code"] == "interrupted"
    assert saved["termination_signal"] == verifier.signal.SIGTERM
    assert verifier.signal.getsignal(verifier.signal.SIGTERM) == previous


def test_success_stdout_stays_compact_while_receipt_contains_full_evidence(tmp_path, capsys):
    receipt = tmp_path / "receipt.json"
    files = [entry("index.html", b"HTML")]
    client = SequenceClient(api_actions(readiness_body()) + [("/new/", b"HTML", {"content_type": "text/html"})])
    assert run_mocked_public(main_arguments(receipt), client, {"files": files})[0] == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["verified"] is True and summary["files_checked"] == 1
    assert "manifest" not in summary and "readiness_attempts" not in summary
    saved = json.loads(receipt.read_text())
    assert saved["manifest"]["files"] == files and saved["readiness_attempts"]


def test_public_image_smoke_requires_local_files_and_uses_manifest_revision(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("public_smoke_stage1", ROOT / "scripts/check_public_smoke.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    root, manifest_path = tmp_path / "dist", tmp_path / "manifest.json"
    root.mkdir()
    with pytest.raises(RuntimeError, match="index/manifest"):
        smoke.check_image_public("https://example.test", image_root=root, manifest_path=manifest_path)
    (root / "index.html").write_text('<script src="/new/main.js"></script>')
    (root / "main.js").write_bytes(b"JS")
    manifest = verifier.manifest_tools.create_manifest(root, REVISION,
        {"node_version": "22.20.0", "pnpm_version": "10.17.1", "lockfile_sha256": "a" * 64})
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setitem(sys.modules, "verify_image_static", verifier)
    with patch.object(smoke, "check_public", return_value={"ready": True}) as check, patch.object(verifier, "verify_http", return_value=manifest["files"]) as hashes:
        assert smoke.check_image_public("https://example.test", image_root=root, manifest_path=manifest_path)["static_files"] == 2
        assert check.call_args.kwargs["expected_release"] == REVISION
        assert hashes.call_args.args[0] == manifest["files"]


def test_retained_image_pin_changes_only_image_and_survives_removed_old_tag(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("primary_image_pin_test", ROOT / "scripts/pin_primary_image.py")
    pin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pin)
    path = tmp_path / REVISION / ".env.primary"
    path.parent.mkdir()
    original = "\n".join(["POSTGRES_PASSWORD=" + "p" * 48, "APP_SECRET_KEY=" + "a" * 64,
        "TOKEN_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(b"t" * 32).decode(),
        "BOOTSTRAP_TOKEN=" + "b" * 40, "PU_RELEASE_REVISION=" + REVISION,
        "PRIMARY_IMAGE=obsolete:deleted-tag", "GMAIL_AUTO_SYNC_ENABLED=true",
        "V54_PRODUCT_AUTO_ENABLED=false"]) + "\n"
    path.write_text(original)
    path.chmod(0o600)
    pin.pin_image(path, IMAGE, REVISION)
    assert path.read_text() == original.replace("PRIMARY_IMAGE=obsolete:deleted-tag", "PRIMARY_IMAGE=" + IMAGE)
    pin.pin_image(path, IMAGE, REVISION)
    spaced = original.replace("PRIMARY_IMAGE=", "  PRIMARY_IMAGE=")
    path.write_text(spaced)
    pin.pin_image(path, IMAGE, REVISION)
    assert path.read_text() == spaced.replace("PRIMARY_IMAGE=obsolete:deleted-tag", "PRIMARY_IMAGE=" + IMAGE)
    with pytest.raises(ValueError, match="retained runtime"):
        pin.pin_image(path, IMAGE, "c" * 40)
