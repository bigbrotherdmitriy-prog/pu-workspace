"""Offline wiring contracts, never PostgreSQL runtime evidence."""
import importlib.util
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def runner():
    spec = importlib.util.spec_from_file_location("remaining_pg_runner", ROOT / "scripts/ci/v54_pilot_workflow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("key,database", [
    ("PUW_V54_AUTHORITY_DATABASE_URL", "puw_v54_test_authority"),
    ("PUW_V54_AUTHORITY_MIGRATION_DATABASE_URL", "puw_v54_test_authority_migration"),
    ("PUW_V54_MATERIALIZATION_DATABASE_URL", "puw_v54_test_materialization"),
    ("PUW_V54_LOCAL_UPLOAD_DATABASE_URL", "puw_v54_test_local_upload"),
])
def test_remaining_fixture_environment_is_owned(monkeypatch, key, database):
    module = runner()
    monkeypatch.setattr(module, "base_url", lambda name: "owned:" + name)
    assert module.test_env().get(key) == "owned:" + database
    assert database in module.DATABASES


@pytest.mark.parametrize("phase", [
    "postgres_authority_migration", "postgres_authority_runtime",
    "postgres_materialization_migration", "postgres_materialization_runtime",
    "postgres_local_upload_runtime", "postgres_schema_fixture",
])
def test_remaining_postgres_phase_is_mandatory(phase):
    module = runner()
    assert phase in module.MANDATORY_POSTGRES


def test_supply_parametrized_proofs_are_all_pinned():
    module = runner()
    assert "postgres_mvp4_supply" in module.MANDATORY_POSTGRES
    assert len(module.MVP_TESTS["postgres_mvp4_supply"]) == 9


@pytest.mark.parametrize("passed,status", [(2, "INCOMPLETE"), (8, "INCOMPLETE"), (9, "PASS")])
def test_supply_requires_all_nine_parameter_variants(monkeypatch, passed, status):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=f"{passed} passed in 0.01s\n", stderr=""))
    if status == "PASS":
        module.run_phase("postgres_mvp4_supply", ["pytest"])
    else:
        with pytest.raises(RuntimeError, match="mandatory_coverage_failed"):
            module.run_phase("postgres_mvp4_supply", ["pytest"])
    assert module.PHASES[-1]["status"] == status
    assert module.PHASES[-1]["required"] == 9


@pytest.mark.parametrize("output,status", [
    ("1 skipped in 0.01s\n", "SKIPPED"),
    ("2 passed in 0.01s\n", "INCOMPLETE"),
    ("no tests ran in 0.01s\n", "INCOMPLETE"),
])
def test_each_remaining_phase_rejects_missing_or_skipped_proof(monkeypatch, output, status):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=output, stderr="synthetic-secret"))
    for phase in module.REMAINING_POSTGRES_TESTS:
        with pytest.raises(RuntimeError, match="mandatory_coverage_failed"):
            module.run_phase(phase, ["pytest"])
        assert module.PHASES[-1]["status"] == status
        assert module.PHASES[-1]["required"] == 1
    assert "synthetic-secret" not in json.dumps(module.PHASES)


def test_remaining_pins_select_real_tests_without_unintended_collection():
    module = runner()
    for nodes in module.REMAINING_POSTGRES_TESTS.values():
        assert len(nodes) == 1
        path, name = nodes[0].split("::")
        tree = ast.parse((ROOT / path).read_text(encoding="utf8"))
        assert name in {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_new_gates_keep_empty_migration_databases_separate_and_opt_in_scoped(monkeypatch):
    module = runner()
    module.CREATED.extend(module.DATABASES)
    monkeypatch.setattr(module, "base_url", lambda database: "owned:" + database)
    env = module.test_env()
    before = dict(env)
    calls = []
    def phase(name, args, **kwargs):
        calls.append((name, args, kwargs["env"]))
    monkeypatch.setattr(module, "run_phase", phase)
    verifications = []
    monkeypatch.setattr(module, "verify_database_head", lambda phase, database: verifications.append((phase, database)))
    module.run_remaining_postgres(env)

    assert env == before
    phases = {name: (args, phase_env) for name, args, phase_env in calls}
    assert set(module.REMAINING_POSTGRES_TESTS) <= set(phases)
    for name, nodes in module.REMAINING_POSTGRES_TESTS.items():
        args, phase_env = phases[name]
        assert args[3:-3] == list(nodes)
        assert phase_env["PU_TEST_POSTGRES"] == ("1" if name == "postgres_schema_fixture" else "0")
    assert [name for name, args, phase_env in calls if "alembic" in args] == ["schema_fixture_migration"]
    assert phases["postgres_materialization_migration"][1]["PUW_V54_MATERIALIZATION_DATABASE_URL"] == "owned:puw_v54_test_materialization_migration"
    assert phases["postgres_materialization_runtime"][1]["PUW_V54_MATERIALIZATION_DATABASE_URL"] == "owned:puw_v54_test_materialization"
    assert phases["postgres_authority_migration"][1]["PUW_V54_AUTHORITY_MIGRATION_DATABASE_URL"] == "owned:puw_v54_test_authority_migration"
    assert phases["postgres_authority_runtime"][1]["PUW_V54_AUTHORITY_DATABASE_URL"] == "owned:puw_v54_test_authority"
    assert phases["postgres_local_upload_runtime"][1]["PUW_V54_LOCAL_UPLOAD_DATABASE_URL"] == "owned:puw_v54_test_local_upload"
    generic_database = phases["postgres_schema_fixture"][1]["DATABASE_URL"]
    assert generic_database == "owned:puw_v54_test_schema_test" and generic_database.endswith("_test")
    names = [name for name, args, phase_env in calls]
    assert names.index("schema_fixture_migration") < names.index("postgres_schema_fixture")
    assert verifications == [
        ("postgres_authority_migration", "puw_v54_test_authority_migration"),
        ("postgres_materialization_migration", "puw_v54_test_materialization_migration"),
        ("schema_fixture_migration", "puw_v54_test_schema_test"),
    ]


def test_remaining_skips_are_reported_and_later_gates_are_not_run(monkeypatch, tmp_path):
    module = runner()
    module.CREATED.extend(module.DATABASES)
    monkeypatch.setattr(module, "base_url", lambda database: "owned:" + database)
    monkeypatch.setattr(module, "OUT", tmp_path / "protocol.json")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="1 skipped in 0.01s\n", stderr="synthetic-secret"))
    with pytest.raises(RuntimeError) as failure:
        module.run_remaining_postgres(module.test_env())
    module.write_protocol("FAIL", failure.value, [])
    protocol = json.loads(module.OUT.read_text())
    assert protocol["mandatory_postgres"]["postgres_authority_migration"] == "SKIPPED"
    assert protocol["mandatory_postgres"]["postgres_schema_fixture"] == "NOT_RUN"
    assert "synthetic-secret" not in module.OUT.read_text()
