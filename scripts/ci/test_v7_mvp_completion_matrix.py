from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "v7_mvp_completion_matrix.py"
SPEC = importlib.util.spec_from_file_location("v7_mvp_completion_matrix", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MATRIX_PATH = ROOT / "docs" / "audits" / "v7-mvp-completion-matrix.json"


def load_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_repository_matrix_is_valid_and_covers_seven_mvps():
    matrix = load_matrix()
    assert MODULE.validate(matrix, ROOT) == []
    assert {item["mvp"] for item in matrix["criteria"]} == set(range(1, 8))


def test_scores_are_deterministic_and_bounded():
    result = MODULE.summarize(load_matrix())
    assert 0 <= result["full_tz_readiness_percent"] <= result["pilot_readiness_percent"] <= 100
    assert result == MODULE.summarize(load_matrix())
    assert sum(item["criteria"] for item in result["by_mvp"].values()) == result["criterion_count"]


def test_verified_runtime_requires_exact_candidate_evidence():
    matrix = load_matrix()
    changed = copy.deepcopy(matrix)
    criterion = changed["criteria"][0]
    criterion["dimensions"]["tested_runtime"] = "verified"
    criterion["evidence"].append({"path": "docs/CURRENT_MVP_AUDIT_RU.md", "kind": "tested_runtime", "sha": "old"})
    errors = MODULE.validate(changed, ROOT)
    assert any("requires evidence at candidate_sha" in error for error in errors)


def test_verified_state_requires_matching_evidence_kind():
    matrix = load_matrix()
    changed = copy.deepcopy(matrix)
    changed["criteria"][0]["dimensions"]["live_provider"] = "verified"
    errors = MODULE.validate(changed, ROOT)
    assert any("live_provider=verified requires matching evidence kind" in error for error in errors)


def test_production_verified_requires_canary():
    matrix = load_matrix()
    changed = copy.deepcopy(matrix)
    criterion = changed["criteria"][0]
    criterion["dimensions"]["production"] = "verified"
    criterion["evidence"].append(
        {"path": "docs/CURRENT_MVP_AUDIT_RU.md", "kind": "production", "sha": changed["candidate_sha"]}
    )
    errors = MODULE.validate(changed, ROOT)
    assert any("production cannot be verified without canary" in error for error in errors)


def test_missing_evidence_file_fails_closed():
    matrix = load_matrix()
    changed = copy.deepcopy(matrix)
    changed["criteria"][0]["evidence"][0]["path"] = "missing/evidence.md"
    assert any("missing evidence path" in error for error in MODULE.validate(changed, ROOT))


def test_completion_decisions_cannot_overstate_evidence():
    matrix = load_matrix()
    pilot = copy.deepcopy(matrix)
    pilot["decisions"]["limited_pilot"]["status"] = "PASS"
    assert any("limited pilot cannot be PASS" in error for error in MODULE.validate(pilot, ROOT))

    full = copy.deepcopy(matrix)
    full["decisions"]["full_tz"]["status"] = "PASS"
    assert any("full TZ cannot be PASS" in error for error in MODULE.validate(full, ROOT))
