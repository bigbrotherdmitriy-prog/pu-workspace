"""CI wiring evidence only; no PostgreSQL runtime PASS is implied."""
import json
from types import SimpleNamespace

import pytest

from test_mvp_runtime_coverage import runner


def test_context_confirmation_has_owned_database_and_exact_mandatory_nodes(monkeypatch):
    module = runner()
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic-only")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("PUW_MVP2_TEST_DATABASE_URL", "unowned-do-not-use")
    assert "puw_mvp2_test_context" in module.DATABASES
    assert "PUW_MVP2_TEST_DATABASE_URL" in module.TEST_DATABASE_KEYS
    assert module.test_env()["PUW_MVP2_TEST_DATABASE_URL"] == module.base_url("puw_mvp2_test_context")
    assert module.MVP_TESTS["postgres_mvp2_context"] == tuple(
        "backend/tests/test_v7_context_confirm_postgres.py::test_pg_context_confirmation_serializes_real_engines[" + case + "]"
        for case in ("duplicate_single", "bulk_vs_single", "failure_then_waiter")
    )
    assert "postgres_mvp2_context" in module.MANDATORY_POSTGRES


@pytest.mark.parametrize("output", ["3 skipped in 0.01s\n", "2 passed in 0.01s\n"])
def test_missing_context_proof_cannot_turn_into_pass(monkeypatch, output):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=output, stderr="synthetic-sensitive-error", returncode=0))
    with pytest.raises(RuntimeError):
        module.run_phase("postgres_mvp2_context", ["pytest"])
    assert "synthetic-sensitive-error" not in json.dumps(module.PHASES)


def test_unrun_context_phase_stays_explicit_in_safe_protocol(monkeypatch, tmp_path):
    module = runner()
    monkeypatch.setattr(module, "OUT", tmp_path / "protocol.json")
    module.write_protocol("FAIL", RuntimeError("synthetic-sensitive-error"), [])
    value = module.OUT.read_text()
    assert json.loads(value)["mandatory_postgres"]["postgres_mvp2_context"] == "NOT_RUN"
    assert "synthetic-sensitive-error" not in value


def test_period_and_retention_proofs_are_mandatory_and_owned(monkeypatch):
    module = runner()
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic-only")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("PUW_V7_AUTOMATION_DATABASE_URL", "unowned-do-not-use")
    assert "puw_v7_test_automation_period" in module.DATABASES
    assert "PUW_V7_AUTOMATION_DATABASE_URL" in module.TEST_DATABASE_KEYS
    assert module.test_env()["PUW_V7_AUTOMATION_DATABASE_URL"] == module.base_url("puw_v7_test_automation_period")
    assert module.MVP_TESTS["postgres_v7_automation_period"] == (
        "backend/tests/test_v7_automation_period_postgres.py::test_postgres_two_manual_days_serialize_to_one_period_pair",
    )
    assert "postgres_v7_automation_period" in module.MANDATORY_POSTGRES
    assert "backend/tests/test_v7_xlsx_retention_recovery.py::test_pg_original_retention_waits_on_project_without_locking_materialization" in module.REMAINING_POSTGRES_TESTS["postgres_local_upload_runtime"]


def test_meeting_nodes_and_integration_branch_cannot_disappear():
    module = runner()
    expected = tuple(
        "backend/tests/test_mvp3_meeting_binding_postgres.py::test_pg_meeting_binding_serializes_actual_commands[" + case + "]"
        for case in ("duplicate_bind", "duplicate_confirm", "bind_vs_stale_edit", "edit_vs_confirm")
    ) + ("backend/tests/test_mvp3_meeting_binding_postgres.py::test_pg_meeting_binding_append_only_is_enforced_by_database",)
    assert tuple(node for node in module.MVP_TESTS["postgres_mvp3_runtime"] if "meeting_binding" in node) == expected
    workflow = (module.ROOT / ".github/workflows/v54-pilot-runtime.yml").read_text(encoding="utf-8")
    assert "- 'codex/v7-execution-wave1'" in workflow
