"""Bounded runner failure is not a runtime success claim."""
import json
from types import SimpleNamespace

import pytest
import yaml

from test_mvp_runtime_coverage import runner


def test_phase_timeout_is_capped_by_global_remainder(monkeypatch):
    module = runner()
    monkeypatch.setattr(module, "WORK_DEADLINE", 110.0, raising=False)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: (
        calls.append(k) or SimpleNamespace(stdout="", stderr="", returncode=0)))
    module.run_phase("corpus", ["synthetic"], timeout=120)
    assert calls[0]["timeout"] == 10.0


def test_expired_budget_starts_no_child_and_preserves_safe_not_run(monkeypatch, tmp_path):
    module = runner()
    monkeypatch.setattr(module, "WORK_DEADLINE", 99.0, raising=False)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("child started"))
    monkeypatch.setattr(module, "OUT", tmp_path / "protocol.json")
    with pytest.raises(RuntimeError):
        module.run_phase("backend_full", ["synthetic-secret"], timeout=900)
    assert module.PHASES[-1]["status"] == "ERROR"
    module.write_protocol("FAIL", RuntimeError("synthetic-secret"), [])
    output = module.OUT.read_text()
    assert "synthetic-secret" not in output
    assert json.loads(output)["mandatory_postgres"]["postgres_mvp2_context"] == "NOT_RUN"


def test_main_finally_resets_deadline_and_cleans_on_expiration(monkeypatch):
    module = runner()
    module.PHASES.append({"name": "old"})
    monkeypatch.setattr(module.ScriptDirectory, "from_config", lambda c: SimpleNamespace(get_heads=lambda: [module.HEAD]))
    monkeypatch.setattr(module, "create_databases", lambda: None)
    monkeypatch.setattr(module, "test_env", lambda: {})
    calls = []
    def expired(*args):
        assert module.WORK_DEADLINE is not None
        assert module.PHASES == []
        raise RuntimeError("budget_expired")
    monkeypatch.setattr(module, "migrate_database", expired)
    monkeypatch.setattr(module, "cleanup_databases", lambda: calls.append("cleanup"))
    monkeypatch.setattr(module, "write_protocol", lambda *args: calls.append(args[0]))
    for _ in range(2):
        with pytest.raises(SystemExit):
            module.main()
        assert module.WORK_DEADLINE is None
        assert module.CLEANUP_DEADLINE is None
    assert calls == ["cleanup", "FAIL", "cleanup", "FAIL"]


def test_workflow_step_reserves_job_time_and_budget_is_fixed():
    module = runner()
    workflow = yaml.safe_load((module.ROOT / ".github/workflows/v54-pilot-runtime.yml").read_text())
    job = workflow["jobs"]["runtime"]
    step = next(s for s in job["steps"] if s.get("run") == "python scripts/ci/v54_pilot_workflow.py")
    assert module.RUNTIME_BUDGET_SECONDS <= 22 * 60
    assert module.CLEANUP_RESERVE_SECONDS >= 60
    assert module.RUNTIME_BUDGET_SECONDS < step["timeout-minutes"] * 60
    assert step["timeout-minutes"] <= 24 < job["timeout-minutes"]


def test_cleanup_deadline_retains_unremoved_owned_ids(monkeypatch):
    module = runner()
    module.CREATED.extend(module.DATABASES[:2])
    monkeypatch.setattr(module, "CLEANUP_DEADLINE", 99.0)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    calls = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params=None):
            calls.append((query, params))
    monkeypatch.setattr(module, "admin_connect", Connection)
    with pytest.raises(RuntimeError, match="owned_database_cleanup_failed"):
        module.cleanup_databases()
    assert module.CREATED == list(module.DATABASES[:2])
    assert calls == [("SET statement_timeout = 1000", None)]


def test_admin_sql_waits_are_bounded(monkeypatch):
    module = runner()
    monkeypatch.setattr(module, "base_url", lambda name: "synthetic")
    calls = []
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: calls.append(k))
    module.admin_connect()
    assert calls == [{"autocommit": True, "connect_timeout": 5,
                      "options": "-cstatement_timeout=5000 -clock_timeout=1000"}]


def test_timeout_exception_cannot_publish_partial_raw_output(monkeypatch):
    module = runner()
    monkeypatch.setattr(module, "WORK_DEADLINE", 110.0)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    def timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(args, kwargs["timeout"],
                                               output="synthetic-document", stderr="synthetic-key")
    monkeypatch.setattr(module.subprocess, "run", timeout)
    with pytest.raises(RuntimeError):
        module.run_phase("backend_full", ["synthetic"], timeout=900)
    assert module.PHASES[-1]["status"] == "ERROR"
    assert "synthetic" not in json.dumps(module.PHASES)
