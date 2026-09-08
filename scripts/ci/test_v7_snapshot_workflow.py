import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def m():
    spec = importlib.util.spec_from_file_location("snapshot_ci", Path(__file__).with_name("v7_snapshot_workflow.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def proof():
    return dict(status="PASS", phase="replay", attempts=2, progress=100, nodes=3,
                forced_kill=True, real_lease_expiry=True, api_process_restart="NOT_RUN",
                provider="synthetic_metadata_only", job_id=1, snapshot_id=2, seconds=65.0)


@pytest.mark.parametrize("change", [{"status": "FAIL"}, {"nodes": True}, {"job_id": True},
    {"raw": "secret"}, {"seconds": float("nan")}, {"api_process_restart": "PASS"}])
def test_protocol_rejects_false_pass(m, change):
    with pytest.raises(ValueError): m.validate_result({**proof(), **change})


def test_valid_proof(m):
    assert m.validate_result(proof()) == proof()


def test_child_failure_phase_accepts_only_exact_safe_protocol(m):
    expected = {"status": "FAIL", "phase": "seed_http", "raw_diagnostics_published": False}
    assert m.child_failure_phase(json.dumps(expected)) == "seed_http"
    for unsafe in (
        {**expected, "detail": "secret"},
        {**expected, "phase": "secret"},
        {**expected, "raw_diagnostics_published": True},
        {**expected, "phase": []},
    ):
        assert m.child_failure_phase(json.dumps(unsafe)) is None


def test_execute_raises_only_allowlisted_child_failure(m, monkeypatch):
    child = Mock(pid=987, returncode=1)
    child.communicate.return_value = (
        json.dumps({"status": "FAIL", "phase": "first_walk", "raw_diagnostics_published": False}),
        "synthetic secret",
    )
    monkeypatch.setattr(m.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(m, "stop_group", Mock())
    with pytest.raises(m.ChildProofFailure) as error:
        m.execute(["synthetic"], {}, m.time.monotonic() + 10)
    assert error.value.phase == "first_walk"
    assert "secret" not in str(error.value)


def test_snapshot_fixture_has_required_connection_identity(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "backend"))
    spec = importlib.util.spec_from_file_location(
        "snapshot_checks_fixture",
        root / "scripts/ci/durable_queue/workspace_snapshot_checks.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connection = module.synthetic_drive_connection(17)
    assert connection.project_id == 17
    assert connection.provider == "google_drive"
    assert connection.account_email == "snapshot-owner@example.invalid"
    assert connection.root_folder_id == "synthetic-customer-project-nested"
    assert connection.connection_id == "synthetic-no-credentials"


def test_snapshot_accepts_only_exact_migrated_bootstrap_organization(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "snapshot_checks_bootstrap",
        root / "scripts/ci/durable_queue/workspace_snapshot_checks.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = SimpleNamespace(name="PU Workspace")
    assert module.migrated_bootstrap_organization([expected]) is expected
    for rows in ([], [expected, expected], [SimpleNamespace(name="Customer data")]):
        with pytest.raises(AssertionError, match="unexpected_bootstrap_organization"):
            module.migrated_bootstrap_organization(rows)


def test_timeout_kills_owned_group(m, monkeypatch):
    child = Mock(pid=987, returncode=None)
    child.communicate.side_effect = subprocess.TimeoutExpired("synthetic", 1)
    spawn = Mock(return_value=child)
    kill = Mock()
    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m.os, "killpg", kill, raising=False)
    monkeypatch.setattr(m.signal, "SIGKILL", 9, raising=False)
    with pytest.raises(subprocess.TimeoutExpired): m.execute(["synthetic"], {}, m.time.monotonic()+10)
    assert spawn.call_args.kwargs["start_new_session"] is True
    kill.assert_called_once_with(987, m.signal.SIGKILL)
    child.wait.assert_called_once_with(timeout=5)


def test_no_spawn_after_deadline(m, monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    with pytest.raises(TimeoutError): m.execute([], {}, 0)
    spawn.assert_not_called()


def test_environment_drops_provider_secrets(m, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic-123")
    monkeypatch.setenv("GOOGLE_TOKEN", "must-not-inherit")
    monkeypatch.setenv("PYTEST_ADDOPTS", "unsafe")
    env = m.environment()
    assert "GOOGLE_TOKEN" not in env and "PYTEST_ADDOPTS" not in env
    assert env["APP_SECRET_KEY"] != m.environment()["APP_SECRET_KEY"]


def setup_main(m, monkeypatch, tmp_path, *, existing=False):
    db = Mock()
    db.__enter__ = Mock(return_value=db)
    db.__exit__ = Mock(return_value=False)
    db.execute.return_value.fetchone.return_value = (1,) if existing else None
    db.execute.return_value.fetchall.return_value = [(m.HEAD,)]
    monkeypatch.setattr(m, "connect", Mock(return_value=db))
    monkeypatch.setattr(m, "environment", lambda: {})
    monkeypatch.setattr(m.os, "name", "posix")
    monkeypatch.setattr(m, "OUT", tmp_path / "protocol.json")
    run = Mock(side_effect=["", json.dumps(proof())])
    monkeypatch.setattr(m, "execute", run)
    return db, run


def test_preexisting_db_never_mutated(m, monkeypatch, tmp_path):
    db, run = setup_main(m, monkeypatch, tmp_path, existing=True)
    assert m.main() == 1
    run.assert_not_called()
    assert len(db.execute.call_args_list) == 1
    assert json.loads(m.OUT.read_text())["cleanup"] == "NOT_NEEDED"


def test_success_and_owned_cleanup(m, monkeypatch, tmp_path):
    db, run = setup_main(m, monkeypatch, tmp_path)
    assert m.main() == 0
    assert "DROP DATABASE" in db.execute.call_args.args[0]
    assert json.loads(m.OUT.read_text())["cleanup"] == "PASS"


@pytest.mark.parametrize("failure", [RuntimeError("secret"), subprocess.TimeoutExpired("secret", 1)])
def test_migration_failure_never_runs_harness(m, monkeypatch, tmp_path, failure):
    db, run = setup_main(m, monkeypatch, tmp_path)
    run.side_effect = failure
    assert m.main() == 1
    assert run.call_count == 1
    assert "DROP DATABASE" in db.execute.call_args.args[0]
    assert "secret" not in m.OUT.read_text()


def test_unconfirmed_process_cleanup_preserves_db(m, monkeypatch, tmp_path):
    db, run = setup_main(m, monkeypatch, tmp_path)
    run.side_effect = m.ContainmentFailure()
    assert m.main() == 1
    assert not any("DROP DATABASE" in c.args[0] for c in db.execute.call_args_list)
    assert json.loads(m.OUT.read_text())["cleanup"] == "FAIL"


def test_database_cleanup_failure_cannot_pass(m, monkeypatch, tmp_path):
    db, run = setup_main(m, monkeypatch, tmp_path)
    result = db.execute.return_value
    def execute(statement, *args):
        if "DROP DATABASE" in statement: raise RuntimeError("sensitive failure")
        return result
    db.execute.side_effect = execute
    assert m.main() == 1
    protocol = json.loads(m.OUT.read_text())
    assert protocol["runtime"] == "PASS" and protocol["cleanup"] == "FAIL"
    assert "sensitive" not in m.OUT.read_text()


def test_workflow_remains_independent_and_bounded():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/v7-snapshot-recovery.yml"
    text = path.read_text()
    assert "timeout-minutes: 12" in text and "timeout-minutes: 8" in text
    assert "if: always()" in text and "if-no-files-found: error" in text
    assert "needs:" not in text and "ports:" not in text and "volumes:" not in text
