"""Static contract for the existing PostgreSQL and Chromium runtime gates."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
BRANCH = "codex/mvp3-postgres-runtime-gate"


def _workflow(name: str) -> tuple[str, dict]:
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf8")
    return text, yaml.safe_load(text)


def _branches(parsed: dict) -> list[str]:
    triggers = parsed.get("on", parsed.get(True))
    return triggers["push"]["branches"]


def test_existing_postgres_workflow_runs_the_mvp3_runtime_phase():
    text, parsed = _workflow("v54-pilot-runtime.yml")
    assert BRANCH in _branches(parsed)
    assert parsed["permissions"] == {"contents": "read"}
    assert "postgres:16-alpine" in text and "ports:" not in text
    runner = (ROOT / "scripts" / "ci" / "v54_pilot_workflow.py").read_text(encoding="utf8")
    assert 'HEAD = "a54f001c0a18"' in runner
    assert '"puw_mvp3_test_runtime"' in runner
    assert '"PUW_MVP3_TEST_DATABASE_URL": base_url("puw_mvp3_test_runtime")' in runner
    assert 'run_phase("postgres_mvp3_runtime"' in runner
    assert '"backend/tests/test_mvp3_management_acceptance_postgres.py"' in runner
    assert '"backend/tests/test_mvp3_management_runtime_postgres.py"' in runner
    assert '"raw_output_published": False' in runner


def test_existing_chromium_workflow_executes_stale_project_guards():
    text, parsed = _workflow("storage-picker-e2e.yml")
    assert BRANCH in _branches(parsed)
    assert parsed["permissions"] == {"contents": "read"}
    assert "pnpm run test:e2e" in text
    e2e = (ROOT / "frontend" / "e2e" / "management-center.e2e.ts").read_text(encoding="utf8")
    assert "a late previous-project response cannot replace the selected project" in e2e
    assert "a late mutation result cannot leak into another project" in e2e
    assert "perform no provider action" in e2e


def test_gate_does_not_create_a_second_queue_or_runtime_workflow():
    workflows = {path.name for path in (ROOT / ".github" / "workflows").glob("*.yml")}
    assert "mvp3-runtime.yml" not in workflows
    assert not (ROOT / "backend" / "app" / "mvp3" / "queue.py").exists()
