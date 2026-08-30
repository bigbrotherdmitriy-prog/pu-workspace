import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_release_package.py"
SPEC = importlib.util.spec_from_file_location("check_release_package", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def repository_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    if not (root / "docker-compose.yml").is_file():
        pytest.skip("commercial package contract is checked from a full repository checkout")
    return root


def test_current_repository_contains_commercial_release_contract():
    root = repository_root()
    checks = MODULE.evaluate(root)
    failed = [row["name"] for row in checks if not row["ok"]]
    assert failed == []


def test_production_candidate_mounts_the_complete_release_contract():
    root = repository_root()
    deploy = (root / "scripts" / "deploy-production.sh").read_text(encoding="utf-8")

    for required in ("Dockerfile", "docker-compose.yml", ".env.example", ".gitignore", "README.md", "docs"):
        assert f'$RELEASE_DIR/{required}' in deploy


def test_preflight_rejects_incomplete_package(tmp_path):
    assert any(not row["ok"] for row in MODULE.evaluate(tmp_path))
