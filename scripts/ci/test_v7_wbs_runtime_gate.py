"""Static wiring checks; PostgreSQL execution remains a separate CI proof."""
import ast
import json
from types import SimpleNamespace

import pytest

from test_mvp_runtime_coverage import runner


def test_wbs_postgres_nodes_are_exact_mandatory_and_defined(monkeypatch):
    module = runner()
    path = "backend/tests/test_v7_schedule_wbs_postgres.py"
    names = (
        "test_pg_wbs_clean_head_and_existing_flat_rows_upgrade",
        "test_pg_wbs_order_constraint_rejects_negative_value",
        "test_pg_wbs_summary_constraint_rejects_leaf_intent",
        "test_pg_wbs_service_rejects_cross_baseline_parent",
        "test_pg_wbs_concurrent_complete_graph_has_one_winner_and_persists_rollup",
        "test_pg_wbs_clone_remaps_parent_and_dependency_ids",
        "test_pg_wbs_downgrade_refuses_hierarchy_intent",
    )
    nodes = module.PINNED_POSTGRES_TESTS["postgres_v7_schedule_wbs"]
    assert len(nodes) == 7
    assert all(node.startswith(path + "::") for node in nodes)
    assert "postgres_v7_schedule_wbs" in module.MANDATORY_POSTGRES
    definitions = {node.name for node in ast.walk(ast.parse(
        (module.ROOT / path).read_text(encoding="utf8"))) if isinstance(node, ast.FunctionDef)}
    assert set(names) <= definitions
    monkeypatch.setattr(module, "base_url", lambda name: "owned:" + name)
    assert module.test_env()["PUW_MVP4_TEST_DATABASE_URL"] == "owned:puw_mvp4_test_runtime"


@pytest.mark.parametrize("output,status", [
    ("7 passed in 1.00s", "PASS"),
    ("6 passed in 1.00s", "INCOMPLETE"),
    ("8 passed in 1.00s", "INCOMPLETE"),
    ("6 passed, 1 skipped in 1.00s", "SKIPPED"),
])
def test_wbs_gate_requires_every_proof_without_skip(monkeypatch, output, status):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=output, stderr="sensitive"))
    if status == "PASS":
        module.run_phase("postgres_v7_schedule_wbs", ["synthetic"])
    else:
        with pytest.raises(RuntimeError, match="mandatory_coverage_failed"):
            module.run_phase("postgres_v7_schedule_wbs", ["synthetic"])
    assert module.PHASES[-1]["status"] == status
    assert "sensitive" not in json.dumps(module.PHASES)


def test_wbs_phase_runs_after_owned_migration_and_protocol_is_content_free():
    module = runner()
    source = (module.ROOT / "scripts/ci/v54_pilot_workflow.py").read_text(encoding="utf8")
    assert source.index('migrate_database("mvp4_migration"') < source.index(
        'run_phase("postgres_v7_schedule_wbs"')
    assert '"wbs": "a20-to-a21 migration' in source
    forbidden = ("payload", "document_text", "email_body", "database_url", "dsn", "password")
    coverage = source.split('"coverage_limits": {', 1)[1].split('},\n        "corpus"', 1)[0].lower()
    assert all(word not in coverage for word in forbidden)
