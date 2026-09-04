from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "legal_release_kit.py"
SPEC = importlib.util.spec_from_file_location("legal_release_kit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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
