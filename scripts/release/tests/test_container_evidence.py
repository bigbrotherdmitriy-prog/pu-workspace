from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "container_evidence.py"
SPEC = importlib.util.spec_from_file_location("container_evidence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_capture_binds_digest_layers_packages_and_sbom(tmp_path):
    digest = "sha256:" + "a" * 64
    image_ref = "registry.example/pu/backend@" + digest
    inspect = tmp_path / "inspect.json"
    dpkg = tmp_path / "dpkg.tsv"
    sbom = tmp_path / "layer.spdx.json"
    inspect.write_text(json.dumps([{
        "Id": "sha256:" + "b" * 64,
        "RepoDigests": [image_ref],
        "RootFS": {"Layers": ["sha256:" + "c" * 64]},
    }]), encoding="utf-8")
    dpkg.write_text("tesseract-ocr\t5.3.0-2\tamd64\n", encoding="utf-8")
    sbom.write_text(json.dumps({
        "spdxVersion": "SPDX-2.3",
        "packages": [{"SPDXID": "SPDXRef-Package", "licenseDeclared": "Apache-2.0"}],
    }), encoding="utf-8")

    value = MODULE.capture(image_ref, inspect, dpkg, sbom, "d" * 40)

    assert value["image_ref"] == image_ref
    assert value["os_packages"][0]["version"] == "5.3.0-2"
    assert value["layer_sbom"]["package_count"] == 1
    assert value["layer_sbom"]["unresolved_license_count"] == 0


def test_tag_only_image_is_rejected(tmp_path):
    with pytest.raises(MODULE.EvidenceError, match="digest-pinned"):
        MODULE.capture("backend:latest", tmp_path / "x", tmp_path / "y", tmp_path / "z", "d" * 40)


@pytest.mark.parametrize("text", ["package-only\n", "bad name\t1\tamd64\n", "pkg\t\tamd64\n"])
def test_dpkg_inventory_is_strict(text):
    with pytest.raises(MODULE.EvidenceError):
        MODULE.parse_dpkg(text)


def test_validator_rejects_unversioned_package():
    value = {
        "schema": "pu-workspace-container-evidence/v1",
        "image_ref": "x@sha256:" + "a" * 64,
        "image_id": "sha256:" + "b" * 64,
        "layer_digests": ["sha256:" + "c" * 64],
        "os_packages": [{"name": "pkg", "version": "", "architecture": "amd64"}],
        "layer_sbom": {"sha256": "d" * 64},
    }
    with pytest.raises(MODULE.EvidenceError, match="unversioned"):
        MODULE.validate(value)
