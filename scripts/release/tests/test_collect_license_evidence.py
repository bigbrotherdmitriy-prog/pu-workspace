from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "collect_license_evidence.py"
SPEC = importlib.util.spec_from_file_location("collect_license_evidence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_npm_record_accepts_valid_expression_and_rejects_free_text():
    resolved = MODULE.npm_record("@scope/pkg", "1.2.3", {"license": "MIT OR Apache-2.0"}, "https://evidence")
    unresolved = MODULE.npm_record("pkg", "2.0.0", {"license": "SEE LICENSE IN LICENSE.txt"}, "https://evidence")

    assert resolved["license_declared"] == "MIT OR Apache-2.0"
    assert resolved["purl"] == "pkg:npm/%40scope/pkg@1.2.3"
    assert unresolved["license_declared"] is None
    assert unresolved["status"] == "unresolved"


def test_generic_bsd_label_is_not_promoted_to_a_specific_spdx_license():
    record = MODULE.pypi_record(
        "Example",
        "1.0",
        {"info": {"license_expression": None, "license": "BSD", "classifiers": ["License :: OSI Approved :: BSD License"]}},
        "https://evidence",
    )

    assert record["license_declared"] is None
    assert record["status"] == "unresolved"


def test_pypi_record_uses_declared_expression_before_classifier_mapping():
    declared = MODULE.pypi_record(
        "Example",
        "1.0",
        {"info": {"license_expression": "BSD-3-Clause", "classifiers": ["License :: OSI Approved :: MIT License"]}},
        "https://evidence",
    )
    classified = MODULE.pypi_record(
        "Example",
        "1.0",
        {"info": {"license_expression": None, "classifiers": ["License :: OSI Approved :: MIT License"]}},
        "https://evidence",
    )

    assert declared["license_declared"] == "BSD-3-Clause"
    assert declared["evidence_kind"] == "pypi-license-expression"
    assert classified["license_declared"] == "MIT"
    assert classified["evidence_kind"] == "pypi-license-classifier"


def test_pypi_record_accepts_valid_legacy_license_field():
    record = MODULE.pypi_record(
        "Example",
        "1.0",
        {"info": {"license_expression": None, "license": "Apache-2.0 OR BSD-3-Clause", "classifiers": []}},
        "https://evidence",
    )

    assert record["license_declared"] == "Apache-2.0 OR BSD-3-Clause"
    assert record["evidence_kind"] == "pypi-license-field"
