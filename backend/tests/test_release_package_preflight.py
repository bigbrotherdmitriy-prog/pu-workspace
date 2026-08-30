import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_release_package.py"
SPEC = importlib.util.spec_from_file_location("check_release_package", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_current_repository_contains_commercial_release_contract():
    root = Path(__file__).resolve().parents[2]
    checks = MODULE.evaluate(root)
    failed = [row["name"] for row in checks if not row["ok"]]
    assert failed == []


def test_preflight_rejects_incomplete_package(tmp_path):
    assert any(not row["ok"] for row in MODULE.evaluate(tmp_path))
