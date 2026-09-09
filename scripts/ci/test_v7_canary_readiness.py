import importlib.util
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
PATH = Path(__file__).with_name("v7_canary_readiness.py")
SPEC = importlib.util.spec_from_file_location("v7_canary_readiness", PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def environment(**updates):
    value = {
        "CI_CANARY_SYNTHETIC_ONLY": "true",
        "CI_CANARY_SCOPE": "run-123-attempt-1",
        "DATABASE_URL": "postgresql://ci:masked@127.0.0.1/puw_canary_test",
    }
    value.update(updates)
    return value


def test_all_daily_work_stages_pass_and_share_a_chained_scope():
    calls = []
    ticks = iter(range(0, 100))
    report = gate.run_gate(environment(), lambda nodeids: calls.append(tuple(nodeids)) or 0, lambda: next(ticks))
    assert report["status"] == "PASS"
    assert report["schema_revision"] == "a54f001c0a22"
    assert report["external_calls"] is False
    assert report["raw_output_included"] is False
    assert [row["stage"] for row in report["stages"]] == [name for name, _ in gate.PROBES]
    assert len({row["scope_receipt"] for row in report["stages"]}) == len(gate.PROBES)
    assert calls == [nodeids for _, nodeids in gate.PROBES]


def test_one_failed_stage_fails_whole_gate_but_keeps_safe_matrix():
    count = 0
    def runner(_nodeids):
        nonlocal count
        count += 1
        return 1 if count == 4 else 0
    report = gate.run_gate(environment(), runner)
    assert report["status"] == "FAIL"
    assert [row["status"] for row in report["stages"]].count("FAIL") == 1
    assert "output" not in json.dumps(report).lower().replace("raw_output_included", "")


@pytest.mark.parametrize(("updates", "reason"), [
    ({"CI_CANARY_SYNTHETIC_ONLY": "false"}, "synthetic_mode_required"),
    ({"CI_CANARY_SCOPE": "production"}, "invalid_canary_scope"),
    ({"DATABASE_URL": "postgresql://x:y@203.0.113.10/puw_canary_test"}, "isolated_database_host_required"),
    ({"DATABASE_URL": "postgresql://x:y@localhost/pu_workspace"}, "isolated_database_name_required"),
    ({"DATABASE_URL": "sqlite:///canary.db"}, "postgresql_required"),
    ({"GOOGLE_ACCESS_TOKEN": "must-not-be-used"}, "provider_credentials_forbidden"),
])
def test_environment_refuses_unsafe_execution(updates, reason):
    with pytest.raises(gate.CanaryRefusal, match=reason):
        gate.run_gate(environment(**updates), lambda _: 0)


def test_probe_contract_covers_requested_chain_and_safety_guards():
    assert [name for name, _ in gate.PROBES] == [
        "storage_binding", "content_analysis", "contract", "wbs", "dds",
        "finance_forecast", "task", "email",
    ]
    selected = " ".join(nodeid for _, nodeids in gate.PROBES for nodeid in nodeids)
    for marker in ("manual_review", "cannot_confirm", "does_not_complete", "cannot_trigger_actions"):
        assert marker in selected
    assert all(nodeid.startswith("backend/tests/") for _, nodeids in gate.PROBES for nodeid in nodeids)


def test_workflow_is_isolated_and_publishes_only_safe_protocol():
    workflow_path = ROOT / ".github/workflows/v7-canary-readiness.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["canary-readiness"]
    assert workflow["permissions"] == {"contents": "read"}
    assert job["services"]["postgres"]["image"] == "postgres:16-alpine"
    assert job["env"]["CI_CANARY_SYNTHETIC_ONLY"] == "true"
    assert "canary" in job["env"]["DATABASE_URL"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "alembic -c alembic.ini upgrade head" in commands
    assert "v7_canary_readiness.py" in commands
    assert all(marker not in commands for marker in ("curl google", "api.telegram", "oauth", "production"))
    upload = next(step for step in job["steps"] if step.get("uses") == "actions/upload-artifact@v4")
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "canary-readiness-artifacts/protocol.json"
    assert upload["with"]["if-no-files-found"] == "error"
