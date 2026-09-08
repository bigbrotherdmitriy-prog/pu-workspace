"""Exact schedule runtime coverage, not a substitute for PostgreSQL execution."""
import ast
from types import SimpleNamespace

import pytest

from test_mvp_runtime_coverage import runner


def test_schedule_proofs_are_explicit_mandatory_and_owned(monkeypatch):
    module = runner()
    names = (
        "test_postgres_graph_cas_allows_one_complete_winner",
        "test_postgres_upgrade_legacy_and_safe_downgrade",
        "test_postgres_downgrade_refuses_graph_intent[active_graph]",
        "test_postgres_downgrade_refuses_graph_intent[legacy_intent]",
    )
    path = "backend/tests/test_v7_schedule_graph_postgres.py"
    assert module.PINNED_POSTGRES_TESTS["postgres_v7_schedule_graph"] == tuple(path + "::" + name for name in names)
    assert "postgres_v7_schedule_graph" in module.MANDATORY_POSTGRES
    definitions = {node.name for node in ast.walk(ast.parse((module.ROOT / path).read_text(encoding="utf8")))
                   if isinstance(node, ast.FunctionDef)}
    assert all(name.split("[")[0] in definitions for name in names)
    monkeypatch.setattr(module, "base_url", lambda name: "owned:" + name)
    assert module.test_env()["PUW_MVP4_TEST_DATABASE_URL"] == "owned:puw_mvp4_test_runtime"
    source = (module.ROOT / "scripts/ci/v54_pilot_workflow.py").read_text(encoding="utf8")
    assert source.index('migrate_database("mvp4_migration"') < source.index('run_phase("postgres_v7_schedule_graph"')


@pytest.mark.parametrize("output,expected", [
    ("4 passed in 1.00s", "PASS"),
    ("3 passed in 1.00s", "INCOMPLETE"),
    ("5 passed in 1.00s", "INCOMPLETE"),
    ("3 passed, 1 skipped in 1.00s", "SKIPPED"),
    ("4 skipped in 1.00s", "SKIPPED"),
    ("4 passed, 1 deselected in 1.00s", "INCOMPLETE"),
])
def test_schedule_gate_rejects_partial_runtime(monkeypatch, output, expected):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=output, stderr=""))
    if expected == "PASS":
        module.run_phase("postgres_v7_schedule_graph", ["synthetic"])
    else:
        with pytest.raises(RuntimeError, match="mandatory_coverage_failed"):
            module.run_phase("postgres_v7_schedule_graph", ["synthetic"])
    assert module.PHASES[-1]["status"] == expected
