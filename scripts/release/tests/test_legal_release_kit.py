from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "legal_release_kit.py"
SPEC = importlib.util.spec_from_file_location("legal_release_kit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

VERIFY_SCRIPT = Path(__file__).resolve().parents[1] / "verify_release_package.py"
VERIFY_SPEC = importlib.util.spec_from_file_location("verify_release_package_for_bundle", VERIFY_SCRIPT)
assert VERIFY_SPEC and VERIFY_SPEC.loader
VERIFY_MODULE = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY_MODULE)


def test_parse_pnpm_lock_stops_before_snapshots_and_keeps_scoped_packages():
    lock = """\
lockfileVersion: '9.0'

packages:

  '@scope/pkg@1.2.3':
    resolution: {integrity: sha512-package}

  plain@4.5.6:
    resolution: {integrity: sha512-plain}
    peerDependencies:
      '@types/react': ^19.0.0
    peerDependenciesMeta:
      '@vitest/ui':
        optional: true

snapshots:

  '@scope/pkg@1.2.3(peer@9.0.0)':
    dependencies:
      peer: 9.0.0

  plain@4.5.6: {}
"""

    assert MODULE.parse_pnpm_lock(lock) == [
        {
            "name": "@scope/pkg",
            "version": "1.2.3",
            "purl": "pkg:npm/%40scope/pkg@1.2.3",
        },
        {
            "name": "plain",
            "version": "4.5.6",
            "purl": "pkg:npm/plain@4.5.6",
        },
    ]


def test_parse_pnpm_lock_deduplicates_only_real_package_entries():
    lock = """\
packages:
  duplicate@1.0.0: {}
  duplicate@1.0.0:
    engines: {node: '>=18'}
snapshots:
  fake@7.0.0: {}
"""

    assert MODULE.parse_pnpm_lock(lock) == [
        {
            "name": "duplicate",
            "version": "1.0.0",
            "purl": "pkg:npm/duplicate@1.0.0",
        }
    ]


def test_parse_hash_lock_preserves_artifact_identity():
    lock = """\
# generated
--only-binary=:all:
--require-hashes
Example_Pkg==1.2.3 \\
    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
"""
    assert MODULE.parse_hash_lock(lock) == [{
        "name": "example-pkg",
        "version": "1.2.3",
        "purl": "pkg:pypi/example-pkg@1.2.3",
        "artifact_sha256": "a" * 64,
    }]


@pytest.mark.parametrize(
    "lock",
    [
        "Example==1.0 " + "\\" + "\n",
        "Example>=1.0\n",
        "Example==1.0 " + "\\" + "\n --hash=sha256:bad\n",
    ],
)
def test_parse_hash_lock_fails_closed(lock):
    with pytest.raises(ValueError):
        MODULE.parse_hash_lock(lock)


def test_spdx_preserves_declared_evidence_but_does_not_invent_legal_conclusion():
    document = MODULE.spdx_document(
        "test",
        "test",
        [{
            "name": "component",
            "version": "1.0.0",
            "purl": "pkg:npm/component@1.0.0",
            "license": "MIT",
            "license_evidence": {
                "status": "resolved-registry-declaration",
                "unresolved_reason": None,
                "evidence_url": "https://registry.example/component/1.0.0",
            },
        }],
    )

    package = document["packages"][0]
    assert package["licenseDeclared"] == "MIT"
    assert package["licenseConcluded"] == "NOASSERTION"
    assert "pending legal review" in package["comment"]


def test_spdx_includes_locked_artifact_checksum():
    document = MODULE.spdx_document(
        "test",
        "test",
        [{
            "name": "component",
            "version": "1.0.0",
            "purl": "pkg:pypi/component@1.0.0",
            "artifact_sha256": "a" * 64,
        }],
    )
    assert document["packages"][0]["checksums"] == [{"algorithm": "SHA256", "checksumValue": "a" * 64}]


def test_license_bundle_reports_each_unresolved_component(tmp_path):
    paths = MODULE.write_license_bundle(
        tmp_path,
        {"container-manifest": [{
            "name": "base-image",
            "version": "tag-not-digest-pinned",
            "purl": "pkg:docker/base-image",
            "license_evidence": {
                "status": "unresolved",
                "unresolved_reason": "image digest and layer inventory are not pinned",
                "evidence_url": None,
            },
        }]},
    )

    matrix = __import__("json").loads(paths[0].read_text(encoding="utf-8"))
    assert matrix["summary"]["unresolved"] == 1
    assert matrix["components"][0]["unresolved_reason"] == "image digest and layer inventory are not pinned"
    notices = paths[1].read_text(encoding="utf-8")
    assert "| container-manifest | `base-image` | `tag-not-digest-pinned` | `UNRESOLVED` |" in notices
    assert "package-specific evidence index" in notices


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(("git", *args), cwd=root, env=env, text=True).strip()


def _release_repo(tmp_path: Path, source: str = "export const label = 'Synthetic';\n") -> Path:
    root = tmp_path / "repo"
    (root / "frontend" / "src").mkdir(parents=True)
    (root / ".env.example").write_text("APP_SECRET_KEY=replace-with-a-random-secret\n", encoding="utf-8")
    (root / "README.md").write_text("Synthetic release\n", encoding="utf-8")
    (root / "frontend" / "src" / "demo.ts").write_text(source, encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "CI")
    _git(root, "add", ".")
    commit_env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
    _git(root, "commit", "-m", "fixture", env=commit_env)
    return root


def test_bundle_is_reproducible_and_manifest_is_bound_to_tree(tmp_path):
    root = _release_repo(tmp_path)
    first, manifest = MODULE.build_bundle(root, tmp_path / "one", "HEAD")
    second, repeated = MODULE.build_bundle(root, tmp_path / "two", "HEAD")

    assert first.read_bytes() == second.read_bytes()
    assert manifest == repeated
    assert manifest["schema"] == "pu-workspace-release-manifest/v2"
    assert manifest["git_tree"] == _git(root, "rev-parse", "HEAD^{tree}")
    assert manifest["secret_pii_scan"] == {"status": "PASS", "findings": 0}
    assert VERIFY_MODULE.verify(first)["status"] == "PASS"


def test_bundle_blocks_client_like_identifier(tmp_path):
    marker = "Налог" + "-Сервис"
    root = _release_repo(tmp_path, f"export const label = '{marker}';\n")
    with pytest.raises(SystemExit, match="client-like identifier"):
        MODULE.build_bundle(root, tmp_path / "out", "HEAD")


def test_env_example_requires_placeholders_for_sensitive_values():
    with pytest.raises(SystemExit, match="not a placeholder"):
        MODULE.validate_env_example(b"APP_SECRET_KEY=real-value\n")


def test_sbom_prefers_linux_hash_lock_over_direct_requirements(tmp_path):
    root = tmp_path / "locked-repo"
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "backend" / "requirements.txt").write_text("direct==1.0\n", encoding="utf-8")
    lock_text = (
        "--only-binary=:all:\n--require-hashes\n"
        "direct==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
        "transitive==2.0 \\\n    --hash=sha256:" + "b" * 64 + "\n"
    )
    (root / "backend" / "requirements-linux-py312.lock").write_text(lock_text, encoding="utf-8")
    generated = root / "docs" / "release" / "generated"
    generated.mkdir(parents=True)
    provenance = {
        "schema": "pu-workspace-python-lock-provenance/v1",
        "target": {
            "implementation_name": "cpython",
            "python_version": "3.12",
            "sys_platform": "linux",
            "platform_machine": "x86_64",
        },
        "requirements_sha256": MODULE.sha256(b"direct==1.0\n"),
        "lock_sha256": MODULE.sha256(lock_text.encode()),
        "package_count": 2,
        "packages": [
            {"name": "direct", "version": "1.0", "sha256": "a" * 64},
            {"name": "transitive", "version": "2.0", "sha256": "b" * 64},
        ],
    }
    (generated / "python-lock-provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    (root / "frontend" / "pnpm-lock.yaml").write_text("packages:\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "backend" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "CI")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "locked fixture")

    paths = MODULE.generate_sbom(root, tmp_path / "sbom", "HEAD")
    backend = __import__("json").loads(paths[0].read_text(encoding="utf-8"))

    assert [row["name"] for row in backend["packages"]] == ["direct", "transitive"]
    assert backend["packages"][1]["checksums"][0]["checksumValue"] == "b" * 64


def test_validated_lock_rejects_provenance_drift(tmp_path):
    root = tmp_path / "drift"
    (root / "backend").mkdir(parents=True)
    generated = root / "docs" / "release" / "generated"
    generated.mkdir(parents=True)
    (root / "backend" / "requirements.txt").write_text("direct==1.0\n", encoding="utf-8")
    (root / "backend" / "requirements-linux-py312.lock").write_text(
        "direct==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    (generated / "python-lock-provenance.json").write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "CI")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "drift")

    with pytest.raises(ValueError, match="unsupported Python lock provenance"):
        MODULE.load_validated_python_lock(root, "HEAD", b"direct==1.0\n")
