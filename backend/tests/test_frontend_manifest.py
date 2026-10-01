import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.frontend_manifest import create_manifest, main, scan_files, validate_files, validate_manifest


REVISION = "0123456789abcdef0123456789abcdef01234567"
BUILD_INFO = {"node_version": "22.20.0", "pnpm_version": "10.17.1",
              "lockfile_sha256": "a" * 64}


@pytest.fixture
def build(tmp_path):
    root = tmp_path / "dist"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><link rel="manifest" href="/new/manifest.webmanifest">'
        '<script src="/new/assets/index-abc.js?v=1#loaded"></script>'
        '<link rel="stylesheet" href="./assets/index-def.css">'
        '<video src="/new/assets/demo.webm" poster="/new/assets/poster.svg"></video>',
        encoding="utf-8",
    )
    (root / "manifest.webmanifest").write_text('{"name":"Frozen build"}', encoding="utf-8")
    (assets / "index-abc.js").write_bytes(b"console.log('frozen');")
    (assets / "index-def.css").write_bytes(b"body{color:#111}")
    (assets / "poster.svg").write_bytes(b"<svg/>")
    (assets / "demo.webm").write_bytes(bytes(range(256)) * 16385)
    (assets / "empty.bin").write_bytes(b"")
    (assets / "\u043f\u0440\u0438\u043c\u0435\u0440.txt").write_text("Unicode asset", encoding="utf-8")
    for index in range(80):
        (assets / f"chunk-{index:03}.js").write_bytes(f"export const value = {index};".encode())
    return root


def test_complete_sorted_frozen_inventory_includes_video_and_every_file(build):
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    assert manifest == create_manifest(build, REVISION, BUILD_INFO, "production")
    assert set(manifest) == {"schema_version", "revision", "build_mode", "build_info", "files"}
    assert manifest["schema_version"] == 1
    assert manifest["build_info"] == BUILD_INFO
    paths = [record["path"] for record in manifest["files"]]
    assert paths == sorted(paths)
    assert len(paths) == 88
    video = next(record for record in manifest["files"] if record["path"] == "assets/demo.webm")
    assert video == {"path": "assets/demo.webm", "size": 256 * 16385,
                     "sha256": hashlib.sha256((build / video["path"]).read_bytes()).hexdigest()}
    assert validate_manifest(manifest, build, REVISION) is manifest
    assert validate_files(build, manifest["files"]) == scan_files(build)


@pytest.mark.parametrize("revision,mode", [("abc", "production"), (REVISION.upper(), "production"),
    ("local-dev", "production"), (REVISION, "development"), ("", "development"),
    (REVISION, "legacy")])
def test_revision_and_mode_must_be_explicit(build, revision, mode):
    with pytest.raises(ValueError):
        create_manifest(build, revision, BUILD_INFO, mode)


def test_development_marker_and_expected_revision(build):
    manifest = create_manifest(build, "local-dev", BUILD_INFO, "development")
    validate_manifest(manifest, build, "local-dev")
    with pytest.raises(ValueError, match="revision mismatch"):
        validate_manifest(manifest, build, REVISION)


@pytest.mark.parametrize("change", [{"lockfile_sha256": None}, {"lockfile_sha256": "A" * 64},
    {"node_version": "unknown"}, {"pnpm_version": ""}, {"image_digest": "sha256:outside"}])
def test_build_metadata_is_required_and_does_not_contain_image_digest(build, change):
    with pytest.raises(ValueError, match="build info|Build info"):
        create_manifest(build, REVISION, {**BUILD_INFO, **change}, "production")


def test_missing_build_metadata_and_unknown_schema_fields(build):
    with pytest.raises(ValueError, match="Build info"):
        create_manifest(build, REVISION, {"node_version": "22.20.0"}, "production")
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    for fields in ({"schema_version": 2}, {"schema_version": True}, {"image_digest": "sha256:outside"}):
        with pytest.raises(ValueError):
            validate_manifest({**manifest, **fields}, build, REVISION)


@pytest.mark.parametrize("path", ["", "../outside", "/absolute", "C:/absolute", "C:\\absolute",
    "assets/../index.html", "assets/./x", "assets//x", "assets/", "./index.html", "assets\\x",
    "assets/name:stream", "assets/\x00name"])
def test_malformed_paths_are_rejected_before_filesystem_access(build, path):
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    manifest["files"][0]["path"] = path
    with pytest.raises(ValueError, match="file path"):
        validate_manifest(manifest, build, REVISION)


def test_empty_inventory_duplicate_paths_and_unsorted_records_are_rejected(build):
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    for files in ([], manifest["files"] + [manifest["files"][0]], list(reversed(manifest["files"]))):
        with pytest.raises(ValueError, match="nonempty|Duplicate|sorted"):
            validate_manifest({**manifest, "files": files}, build, REVISION)


@pytest.mark.parametrize("change", [{"size": True}, {"size": -1}, {"size": "5"},
    {"sha256": "A" * 64}, {"sha256": "short"}, {"extra": "untrusted"}])
def test_malformed_file_records_are_rejected(build, change):
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    manifest["files"][0].update(change)
    with pytest.raises(ValueError):
        validate_manifest(manifest, build, REVISION)


def test_same_size_corruption_is_detected(build):
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    asset = build / "assets/chunk-079.js"
    asset.write_bytes(b"X" * asset.stat().st_size)
    with pytest.raises(ValueError, match="sha256 mismatch"):
        validate_manifest(manifest, build, REVISION)


def test_size_corruption_is_detected(build):
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    (build / "assets/chunk-079.js").write_bytes(b"corrupted size")
    with pytest.raises(ValueError, match="size or sha256 mismatch"):
        validate_manifest(manifest, build, REVISION)


@pytest.mark.parametrize("mutation", ["extra", "missing"])
def test_inventory_cannot_hide_extra_or_missing_files(build, mutation):
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    if mutation == "extra":
        (build / "unlisted-secret.txt").write_text("unexpected", encoding="utf-8")
    else:
        (build / "assets/chunk-079.js").unlink()
    with pytest.raises(ValueError, match="inventory differs"):
        validate_manifest(manifest, build, REVISION)


@pytest.mark.parametrize("kind", ["file", "directory", "root", "dangling"])
def test_symlinks_are_rejected(build, tmp_path, kind):
    target = tmp_path / "external"
    target.mkdir()
    (target / "external.js").write_text("external", encoding="utf-8")
    link = tmp_path / "linked-dist" if kind == "root" else build / "assets/link"
    try:
        link.symlink_to(target if kind == "directory" else build if kind == "root"
                        else target / ("missing.js" if kind == "dangling" else "external.js"),
                        target_is_directory=kind in {"directory", "root"})
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Host cannot create symlinks: {exc}")
    with pytest.raises(ValueError, match="symlink|junction"):
        scan_files(link if kind == "root" else build)


@pytest.mark.parametrize("reference", ["/new/assets/missing.js", "../outside.js",
    "/new/assets/%2e%2e/index.html", "file:///outside.js", "file://server/outside.js", ""])
def test_index_asset_references_must_be_safe_and_present(build, reference):
    (build / "index.html").write_text(f'<script src="{reference}"></script>', encoding="utf-8")
    with pytest.raises(ValueError, match="asset|file path"):
        create_manifest(build, REVISION, BUILD_INFO, "production")


def test_index_reference_corruption_rejected_even_when_inventory_is_rehashed(build):
    manifest = create_manifest(build, REVISION, BUILD_INFO, "production")
    index = build / "index.html"
    index.write_text('<link rel="stylesheet" href="/new/assets/missing.css">', encoding="utf-8")
    record = next(record for record in manifest["files"] if record["path"] == "index.html")
    record.update(size=index.stat().st_size, sha256=hashlib.sha256(index.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="Missing index.html asset"):
        validate_manifest(manifest, build, REVISION)


def test_external_urls_do_not_require_local_files(build):
    (build / "index.html").write_text(
        '<script src="https://example.invalid/app.js"></script>'
        '<img src="data:image/png;base64,AA=="><link rel="canonical" href="/new/">', encoding="utf-8")
    validate_manifest(create_manifest(build, REVISION, BUILD_INFO), build, REVISION)


def test_srcset_data_urls_and_local_assets_are_checked(build):
    index = build / "index.html"
    index.write_text('<img srcset="data:image/png;base64,AA== 1x, /new/assets/poster.svg 2x">', encoding="utf-8")
    validate_manifest(create_manifest(build, REVISION, BUILD_INFO), build, REVISION)
    index.write_text('<source srcset="/new/assets/poster.svg, /new/assets/missing.svg 2x">', encoding="utf-8")
    with pytest.raises(ValueError, match="Missing index.html asset"):
        create_manifest(build, REVISION, BUILD_INFO)


def test_ambiguous_index_base_and_duplicate_attributes_are_rejected(build):
    index = build / "index.html"
    for html in ('<base href="https://example.invalid/">',
                 '<script src="/new/assets/index-abc.js" src="/new/assets/hidden.js"></script>'):
        index.write_text(html, encoding="utf-8")
        with pytest.raises(ValueError, match="base href|Duplicate asset attribute"):
            create_manifest(build, REVISION, BUILD_INFO)


def test_empty_build_and_missing_index_are_rejected(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(ValueError, match="empty"):
        scan_files(root)
    (root / "asset.js").write_bytes(b"content")
    with pytest.raises(ValueError, match="missing index.html"):
        scan_files(root)


def test_manifest_owns_metadata_copy(build):
    info = copy.deepcopy(BUILD_INFO)
    manifest = create_manifest(build, REVISION, info)
    info["node_version"] = "0.0.0"
    assert manifest["build_info"] == BUILD_INFO


def test_cli_json_file_and_inline_metadata_are_supported_outside_public_root(build, tmp_path):
    info_file, output = tmp_path / "build-info.json", tmp_path / "frontend-build-manifest.json"
    info_file.write_text(json.dumps(BUILD_INFO), encoding="utf-8")
    arguments = ["create", "--root", str(build), "--output", str(output), "--revision", REVISION,
                 "--build-info", str(info_file), "--mode", "production"]
    completed = subprocess.run([sys.executable, "-m", "scripts.frontend_manifest", *arguments],
                               cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    validate_manifest(manifest, build, REVISION)
    assert not (build / output.name).exists()
    arguments[arguments.index(str(info_file))] = json.dumps(BUILD_INFO)
    assert main(arguments) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == manifest


def test_cli_rejects_output_in_public_root_before_writing(build):
    output = build / "frontend-build-manifest.json"
    with pytest.raises(SystemExit) as error:
        main(["create", "--root", str(build), "--output", str(output), "--revision", REVISION,
              "--build-info", json.dumps(BUILD_INFO)])
    assert error.value.code == 1
    assert not output.exists()


def test_cli_rejects_duplicate_json_keys(build, tmp_path):
    output = tmp_path / "frontend-build-manifest.json"
    duplicate = '{"node_version":"22.20.0","node_version":"20.0.0",' + json.dumps(BUILD_INFO)[1:]
    with pytest.raises(SystemExit) as error:
        main(["create", "--root", str(build), "--output", str(output), "--revision", REVISION,
              "--build-info", duplicate])
    assert error.value.code == 1
    assert not output.exists()
