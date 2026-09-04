from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "verify_release_package.py"
SPEC = importlib.util.spec_from_file_location("verify_release_package", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def archive(
    tmp_path: Path,
    files: dict[str, bytes],
    *,
    unsafe: tarfile.TarInfo | None = None,
    corrupt_manifest: bool = False,
) -> Path:
    commit = "a" * 40
    root = f"pu-workspace-{commit[:12]}"
    rows = [
        {"path": path, "size": len(data), "sha256": MODULE.sha256_bytes(data)}
        for path, data in sorted(files.items())
    ]
    if corrupt_manifest:
        rows[0]["sha256"] = "0" * 64
    manifest = json.dumps({
        "schema": "pu-workspace-release-manifest/v2",
        "git_commit": commit,
        "files": rows,
    }).encode()
    target = tmp_path / "release.tar.gz"
    with tarfile.open(target, "w:gz") as out:
        for path, data in {**files, "docs/release/generated/RELEASE_MANIFEST.json": manifest}.items():
            info = tarfile.TarInfo(f"{root}/{path}")
            info.size = len(data)
            out.addfile(info, io.BytesIO(data))
        if unsafe:
            out.addfile(unsafe, io.BytesIO(b"x") if unsafe.size else None)
    return target


def clean_files() -> dict[str, bytes]:
    return {
        ".env.example": b"APP_SECRET_KEY=replace-with-at-least-32-random-characters\nGOOGLE_CLIENT_SECRET=\n",
        "README.md": b"Synthetic release\n",
    }


def test_clean_archive_passes(tmp_path):
    result = MODULE.verify(archive(tmp_path, clean_files()))
    assert result["status"] == "PASS"
    assert result["secret_pii_findings"] == 0


@pytest.mark.parametrize(
    "path,data,reason",
    [
        ("README.md", b"token " + b"ghp_" + b"a" * 30, "GitHub token"),
        ("README.md", ("Customer: " + "Налог" + "-Сервис").encode(), "known client marker"),
        ("README.md", b"postgresql://user:secret@db/app", "credential-bearing DSN"),
        (".env.example", b"APP_SECRET_KEY=real-secret-value\n", "placeholder"),
    ],
)
def test_sensitive_content_is_rejected(tmp_path, path, data, reason):
    files = clean_files()
    files[path] = data
    with pytest.raises(MODULE.PackageError, match=reason):
        MODULE.verify(archive(tmp_path, files))


def test_symlink_member_is_rejected(tmp_path):
    unsafe = tarfile.TarInfo("pu-workspace-aaaaaaaaaaaa/link")
    unsafe.type = tarfile.SYMTYPE
    unsafe.linkname = "../../outside"
    with pytest.raises(MODULE.PackageError, match="non-regular"):
        MODULE.verify(archive(tmp_path, clean_files(), unsafe=unsafe))


def test_manifest_hash_mismatch_is_rejected(tmp_path):
    with pytest.raises(MODULE.PackageError, match="manifest hash mismatch"):
        MODULE.verify(archive(tmp_path, clean_files(), corrupt_manifest=True))
