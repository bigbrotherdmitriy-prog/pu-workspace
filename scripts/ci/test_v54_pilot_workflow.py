from pathlib import Path
from dataclasses import dataclass
import importlib.util
import pickle
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PicklePolicy:
    authority: object


def test_v54_workflow_is_branch_scoped_and_has_safe_artifact():
    path = ROOT / ".github/workflows/v54-pilot-runtime.yml"
    text = path.read_text(encoding="utf8")
    # PyYAML 1.1 may coerce `on`; textual trigger checks are intentional too.
    parsed = yaml.safe_load(text)
    assert parsed["permissions"] == {"contents": "read"}
    assert "branches: ['codex/v54-final-integration']" in text
    assert "workflow_dispatch:" in text and "pull_request:" not in text
    assert "persist-credentials: false" in text
    assert "postgres:16-alpine" in text and "ports:" not in text
    upload = next(
        step for step in parsed["jobs"]["runtime"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["with"]["path"] == "v54-runtime-artifacts/protocol.json"
    assert "backend/app/react_dist" not in text


def test_runtime_orchestrator_never_publishes_captured_output_or_secrets():
    source = (ROOT / "scripts/ci/v54_pilot_workflow.py").read_text(encoding="utf8")
    assert '"raw_published": False' in source
    assert '"raw_output_published": False' in source
    assert "capture_output=True" in source
    assert "print(result.stdout" not in source and "print(result.stderr" not in source
    assert "DROP DATABASE {}" in source and "pg_terminate_backend" in source
    assert 'HEAD = "a54f001c0a02"' in source


def test_postgres_phase_requests_safe_failure_summary():
    source = (ROOT / "scripts/ci/v54_pilot_workflow.py").read_text(encoding="utf8")
    postgres_phase = source.split('run_phase("postgres_abc_integration"', 1)[1].split("env=env", 1)[0]
    assert '"-rfsE"' in postgres_phase


def test_failed_phase_records_only_safe_pytest_nodeids(monkeypatch):
    path = ROOT / "scripts/ci/v54_pilot_workflow.py"
    spec = importlib.util.spec_from_file_location("v54_pilot_workflow_failure_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.PHASES.clear()
    secret = "synthetic-document-secret"
    stdout = (
        "backend/tests/test_v54_authority_postgres.py:104: in test_revoke_wins\n"
        f"    unsafe detail: {secret}\n"
        "FAILED backend/tests/test_v54_authority_postgres.py::test_revoke_wins "
        f"- AssertionError: {secret}\n1 failed, 273 passed in 1.00s\n"
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout=stdout, stderr=f"provider response: {secret}"
        ),
    )
    with pytest.raises(RuntimeError, match="postgres_abc_integration_failed"):
        module.run_phase("postgres_abc_integration", ["pytest"])

    encoded = str(module.PHASES)
    assert module.PHASES[0]["failed_nodeids"] == [
        "backend/tests/test_v54_authority_postgres.py::test_revoke_wins"
    ]
    assert module.PHASES[0]["failure_locations"] == [
        "backend/tests/test_v54_authority_postgres.py:104"
    ]
    assert secret not in encoded


def test_process_probe_records_only_allowlisted_failure_checkpoint(monkeypatch):
    path = ROOT / "scripts/ci/v54_pilot_workflow.py"
    spec = importlib.util.spec_from_file_location("v54_pilot_workflow_probe_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.PHASES.clear()
    secret = "synthetic-document-secret"
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout='{"status":"FAIL","error_type":"AssertionError","checkpoint":"lease_recovery"}\n',
            stderr=secret,
        ),
    )
    with pytest.raises(RuntimeError, match="postgres_process_fault_failed"):
        module.run_phase("postgres_process_fault", ["python"])

    assert module.PHASES[0]["failure_checkpoint"] == "lease_recovery"
    assert module.PHASES[0]["child_error_type"] == "AssertionError"
    assert secret not in str(module.PHASES)


def test_successful_probe_cleanup_preserves_prior_checkpoint():
    path = ROOT / "scripts/ci/v54_pilot_runtime.py"
    spec = importlib.util.spec_from_file_location("v54_pilot_runtime_cleanup_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.checkpoint("first_claim")
    child = SimpleNamespace(is_alive=lambda: False, join=lambda _timeout: None)
    fixture = SimpleNamespace(close=lambda: None)

    module.cleanup_probe([child], fixture)

    assert module.CHECKPOINT == "first_claim"


def test_process_probe_cleanup_tolerates_process_not_started():
    path = ROOT / "scripts/ci/v54_pilot_runtime.py"
    spec = importlib.util.spec_from_file_location("v54_pilot_runtime_unstarted_cleanup_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    closed = []
    child = SimpleNamespace(
        pid=None,
        is_alive=lambda: False,
        join=lambda _timeout: (_ for _ in ()).throw(AssertionError("not started")),
    )
    fixture = SimpleNamespace(close=lambda: closed.append(True))

    module.cleanup_probe([child], fixture)

    assert closed == [True]


def test_process_probe_strips_unpicklable_authority_before_spawn():
    path = ROOT / "scripts/ci/v54_pilot_runtime.py"
    spec = importlib.util.spec_from_file_location("v54_pilot_runtime_policy_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    policy = PicklePolicy(authority=lambda: None)

    safe_policy = module.spawn_policy(policy)

    assert safe_policy.authority is None
    pickle.dumps(safe_policy)


def test_runtime_orchestrator_always_cleans_created_databases(monkeypatch, tmp_path):
    path = ROOT / "scripts/ci/v54_pilot_workflow.py"
    spec = importlib.util.spec_from_file_location("v54_pilot_workflow_cleanup_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    cleanup_calls = []
    written = {}
    monkeypatch.setattr(module.ScriptDirectory, "from_config", lambda _config: type("Heads", (), {"get_heads": lambda self: [module.HEAD]})())
    monkeypatch.setattr(module, "create_databases", lambda: module.CREATED.append(module.DATABASES[0]))
    monkeypatch.setattr(module, "test_env", lambda: {})
    monkeypatch.setattr(module, "base_url", lambda database: f"postgresql+psycopg://test@postgres/{database}")
    monkeypatch.setattr(module, "run_phase", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic_failure")))
    monkeypatch.setattr(module, "cleanup_databases", lambda: cleanup_calls.append(tuple(module.CREATED)))
    monkeypatch.setattr(module, "write_protocol", lambda result, failure, runtime: written.update(result=result, failure=failure, runtime=runtime))

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 1
    assert cleanup_calls == [(module.DATABASES[0],)]
    assert module.CREATED == []
    assert written["result"] == "FAIL"
    assert isinstance(written["failure"], RuntimeError)
