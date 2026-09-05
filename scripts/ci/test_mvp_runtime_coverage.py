"""Offline contracts; these mocks are not PostgreSQL runtime evidence."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def runner():
    spec = importlib.util.spec_from_file_location("mvp_runtime_coverage_runner", ROOT / "scripts/ci/v54_pilot_workflow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mvp1_owned_postgres_environment_is_not_missing(monkeypatch):
    module = runner()
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic-ci-password")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    env = module.test_env()
    assert env.get("TEST_POSTGRES_DSN") == module.base_url("puw_v54_test_storage")
    assert "puw_v54_test_storage" in module.DATABASES


def test_mvp1_postgres_tests_are_mandatory_in_existing_runner():
    source = (ROOT / "scripts/ci/v54_pilot_workflow.py").read_text(encoding="utf8")
    assert '"backend/tests/test_mvp1_storage_mutation_postgres_runtime.py"' in source
    assert '"postgres_mvp1_storage"' in source


@pytest.mark.parametrize("output,status", [
    ("2 skipped in 0.01s\n", "SKIPPED"),
    ("1 passed, 1 skipped in 0.01s\n", "SKIPPED"),
    ("1 passed in 0.01s\n", "INCOMPLETE"),
    ("1 passed, 1 xfailed in 0.01s\n", "INCOMPLETE"),
    ("2 passed, 1 deselected in 0.01s\n", "INCOMPLETE"),
    ("raw-secret says 2 passed\n", "INCOMPLETE"),
])
def test_mandatory_phase_rejects_skipped_or_missing_proofs(monkeypatch, output, status):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=output, stderr="raw-secret", returncode=0))
    with pytest.raises(RuntimeError, match="mandatory_coverage_failed"):
        module.run_phase("postgres_mvp1_storage", ["pytest"])
    assert module.PHASES[-1]["status"] == status
    assert "raw-secret" not in json.dumps(module.PHASES)


def test_mandatory_success_requires_all_proofs_and_no_skips(monkeypatch):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="2 passed, 3 warnings in 0.01s\n", stderr="", returncode=0))
    module.run_phase("postgres_mvp1_storage", ["pytest"])
    assert module.PHASES[-1]["status"] == "PASS"
    assert module.PHASES[-1]["passed"] == module.PHASES[-1]["required"] == 2


@pytest.mark.parametrize("returncode,output,status", [
    (1, "1 failed, 1 passed in 0.01s\n", "FAIL"),
    (4, "ERROR: missing test node\n", "FAIL"),
    (5, "no tests ran in 0.01s\n", "FAIL"),
])
def test_mandatory_test_or_collection_failure_is_recorded(monkeypatch, returncode, output, status):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=output, stderr="raw-secret", returncode=returncode))
    with pytest.raises(RuntimeError):
        module.run_phase("postgres_mvp4_finance", ["pytest"])
    assert module.PHASES[-1]["status"] == status


def test_timeout_is_safe_and_distinct_from_not_run(monkeypatch):
    module = runner()
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(["raw-secret"], 1, output="raw-secret")
    monkeypatch.setattr(module.subprocess, "run", timed_out)
    with pytest.raises(RuntimeError):
        module.run_phase("postgres_mvp1_storage", ["pytest"])
    assert module.PHASES[-1]["status"] == "ERROR"
    assert "raw-secret" not in json.dumps(module.PHASES)


def test_environment_cannot_inherit_other_databases_or_pytest_filter(monkeypatch):
    module = runner()
    for key in (*module.TEST_DATABASE_KEYS, "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        monkeypatch.setenv(key, "unowned-value")
    monkeypatch.setenv("PU_TEST_POSTGRES", "1")
    monkeypatch.setattr(module, "base_url", lambda name: "owned:" + name)
    env = module.test_env()
    assert env["DATABASE_URL"] == "sqlite+pysqlite:///:memory:"
    assert env["PU_TEST_POSTGRES"] == "0"
    assert env["PUW_MVP4_TEST_DATABASE_URL"] == "owned:puw_mvp4_test_runtime"
    assert "unowned-value" not in env.values()


def test_cleanup_continues_after_one_owned_drop_fails(monkeypatch):
    module = runner()
    first, second, third = module.DATABASES[:3]
    module.CREATED[:] = [first, second, third]
    attempted = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params=None):
            if params is not None:
                attempted.append(params[0])
                if params[0] == second:
                    raise RuntimeError("raw-secret")
    monkeypatch.setattr(module, "admin_connect", Connection)
    with pytest.raises(RuntimeError, match="owned_database_cleanup_failed"):
        module.cleanup_databases()
    assert attempted == [third, second, first]
    assert module.CREATED == [second]


def test_preexisting_database_is_never_claimed_or_removed(monkeypatch):
    module = runner()
    calls = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params=None):
            calls.append(query)
            return [(module.DATABASES[0],)]
    monkeypatch.setattr(module, "admin_connect", Connection)
    with pytest.raises(RuntimeError, match="test_database_preexists"):
        module.create_databases()
    module.cleanup_databases()
    assert module.CREATED == [] and len(calls) == 1


def test_protocol_distinguishes_mandatory_not_run_and_skip(monkeypatch, tmp_path):
    module = runner()
    module.PHASES.append({"name": "postgres_mvp1_storage", "status": "SKIPPED", "exit": 0})
    monkeypatch.setattr(module, "OUT", tmp_path / "protocol.json")
    module.write_protocol("FAIL", RuntimeError("raw-secret"), [])
    protocol = json.loads(module.OUT.read_text())
    assert protocol["mandatory_postgres"]["postgres_mvp1_storage"] == "SKIPPED"
    assert protocol["mandatory_postgres"]["postgres_mvp4_finance"] == "NOT_RUN"
    assert "raw-secret" not in module.OUT.read_text()


@pytest.mark.parametrize("host,github_actions", [("example.com", "true"), ("postgres", "false")])
def test_database_host_is_local_or_ci_service_only(monkeypatch, host, github_actions):
    module = runner()
    monkeypatch.setenv("POSTGRES_HOST", host)
    monkeypatch.setenv("GITHUB_ACTIONS", github_actions)
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic")
    with pytest.raises(RuntimeError, match="unsafe_database_host"):
        module.base_url(module.DATABASES[0])


def test_main_wires_exact_mandatory_nodes_after_owned_head_migrations(monkeypatch):
    module = runner()
    phases = []
    cleanups = []
    monkeypatch.setattr(module.ScriptDirectory, "from_config", lambda config: SimpleNamespace(
        get_heads=lambda: [module.HEAD]))
    monkeypatch.setattr(module, "create_databases", lambda: module.CREATED.extend(module.DATABASES))
    monkeypatch.setattr(module, "base_url", lambda name: "owned:" + name)
    class Database:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query):
            assert query == "SELECT version_num FROM alembic_version"
            return SimpleNamespace(fetchone=lambda: (module.HEAD,))
    monkeypatch.setattr(module.psycopg, "connect", lambda *args, **kwargs: Database())
    def run(name, args, **kwargs):
        phases.append((name, args, kwargs))
        return '{"structural":"PASS","cases":28}' if name == "corpus" else ""
    monkeypatch.setattr(module, "run_phase", run)
    monkeypatch.setattr(module, "parse_runtime_output", lambda output: [])
    monkeypatch.setattr(module, "cleanup_databases", lambda: cleanups.append(tuple(module.CREATED)))
    monkeypatch.setattr(module, "write_protocol", lambda result, failure, runtime: None)
    module.main()
    names = [phase[0] for phase in phases]
    for phase, database, migration in (
        ("postgres_mvp1_storage", "puw_v54_test_storage", "storage_migration"),
        ("postgres_gmail_history", "puw_mvp2_test_gmail_history", "gmail_history_migration"),
        ("postgres_mvp3_runtime", "puw_mvp3_test_runtime", "mvp3_migration"),
        ("postgres_mvp4_finance", "puw_mvp4_test_runtime", "mvp4_migration"),
    ):
        assert names.index(migration) < names.index(phase)
        _, migration_args, options = phases[names.index(migration)]
        assert migration_args[-2:] == ["upgrade", "a54f001c0a18"]
        assert options["env"]["DATABASE_URL"] == "owned:" + database
        assert options["cwd"] == module.ROOT / "backend"
        _, test_args, _ = phases[names.index(phase)]
        assert test_args[3:-3] == list(module.MVP_TESTS[phase])
    _, _, offline_options = phases[names.index("backend_full")]
    assert all(offline_options["env"][key] == "" for key in module.TEST_DATABASE_KEYS)
    assert cleanups == [module.DATABASES]


def test_migration_refuses_database_not_created_by_this_run():
    module = runner()
    with pytest.raises(RuntimeError, match="migration_database_not_owned"):
        module.migrate_database("mvp4_migration", "puw_mvp4_test_runtime", {})


def test_migration_verifies_database_version_not_only_subprocess_exit(monkeypatch):
    module = runner()
    database = "puw_mvp4_test_runtime"
    module.CREATED.append(database)
    monkeypatch.setattr(module, "base_url", lambda name: "owned:" + name)
    monkeypatch.setattr(module, "run_phase", lambda *args, **kwargs: "")
    class Database:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query):
            return SimpleNamespace(fetchone=lambda: ("a54f001c0a17",))
    monkeypatch.setattr(module.psycopg, "connect", lambda *args, **kwargs: Database())
    with pytest.raises(RuntimeError, match="migration_head_mismatch"):
        module.migrate_database("mvp4_migration", database, {})
