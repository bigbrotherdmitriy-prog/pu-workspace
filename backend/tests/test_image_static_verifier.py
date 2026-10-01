import hashlib
import importlib.util
import io
import json
import base64
import sys
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
        assert verifier.main(arguments + ["--compose-project", "puw-primary-next"]) == 1
        assert "archive-sha256" in capsys.readouterr().err
    assert not (tmp_path / "receipt.json").exists()


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
