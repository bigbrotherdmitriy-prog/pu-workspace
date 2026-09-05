from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HEAD = "a54f001c0a13"
WAVE3_BRANCH = "codex/v54-wave3-integration"


def _workflow(name: str) -> tuple[dict, str]:
    text = (ROOT / ".github/workflows" / name).read_text(encoding="utf8")
    return yaml.safe_load(text), text


def _triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


def test_wave3_runs_postgres_runtime_and_durable_fault_gate():
    runtime, _ = _workflow("v54-pilot-runtime.yml")
    durable, _ = _workflow("durable-queue.yml")
    assert WAVE3_BRANCH in _triggers(runtime)["push"]["branches"]
    assert WAVE3_BRANCH in _triggers(durable)["push"]["branches"]
    assert runtime["permissions"] == {"contents": "read"}
    assert durable["permissions"] == {"contents": "read"}


def test_every_wave3_runtime_pin_matches_a09():
    sources = {
        "orchestrator": ROOT / "scripts/ci/v54_pilot_workflow.py",
        "durable": ROOT / "scripts/ci/durable_queue/run.py",
        "compose_smoke": ROOT / ".github/workflows/docker-smoke.yml",
    }
    for label, path in sources.items():
        text = path.read_text(encoding="utf8")
        assert EXPECTED_HEAD in text, label
        assert "a54f001c0a08" not in text, label


def test_wave3_artifacts_are_exact_allowlisted_json_files():
    runtime, _ = _workflow("v54-pilot-runtime.yml")
    durable, _ = _workflow("durable-queue.yml")
    runtime_upload = next(
        step for step in runtime["jobs"]["runtime"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    durable_upload = next(
        step for step in durable["jobs"]["recovery"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert runtime_upload["with"]["path"] == "v54-runtime-artifacts/protocol.json"
    assert durable_upload["with"]["path"].splitlines() == [
        "queue-artifacts/protocol.json",
        "queue-artifacts/fallback-cleanup.json",
    ]
    for upload in (runtime_upload, durable_upload):
        assert upload["if"] == "always()"
        assert upload["with"]["retention-days"] == 7


def test_cleanup_is_unconditional_and_precedes_durable_artifact():
    durable, _ = _workflow("durable-queue.yml")
    steps = durable["jobs"]["recovery"]["steps"]
    cleanup = next(index for index, step in enumerate(steps) if "cleanup.py" in step.get("run", ""))
    upload = next(index for index, step in enumerate(steps)
                  if str(step.get("uses", "")).startswith("actions/upload-artifact@"))
    assert steps[cleanup]["if"] == "always()"
    assert cleanup < upload
    orchestrator = (ROOT / "scripts/ci/v54_pilot_workflow.py").read_text(encoding="utf8")
    assert "finally:" in orchestrator
    assert "cleanup_databases()" in orchestrator
    assert '"cleanup": "PASS" if not CREATED else "FAIL"' in orchestrator


def test_durable_protocol_does_not_capture_subprocess_arguments_or_raw_output():
    runner = (ROOT / "scripts/ci/durable_queue/run.py").read_text(encoding="utf8")
    assert '"command": args' not in runner
    assert '"operation": safe_operation(args)' in runner
    assert '"raw_published": False' in runner
    assert "capture_output=True" in runner
    assert "stdout.decode" not in runner and "stderr.decode" not in runner
