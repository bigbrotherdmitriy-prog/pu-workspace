from pathlib import Path
import ast
import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_final_candidate_push_is_branch_scoped_and_read_only():
    workflow = yaml.safe_load((ROOT / ".github/workflows/durable-queue.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["push"]["branches"] == [
        "codex/parallel-validation-final",
        "codex/v54-wave3-integration",
        "codex/v54-wave4-integration",
    ]
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request" in triggers and "workflow_dispatch" in triggers
    assert workflow["jobs"]["recovery"]["timeout-minutes"] <= 30


def test_isolated_topology_and_no_credentials_or_ports():
    cfg = yaml.safe_load((ROOT / "docker-compose.queue-ci.yml").read_text())
    assert set(cfg["services"]) == {"db", "api1", "api2", "worker1", "worker2", "scheduler"}
    assert cfg["networks"] == {"ci": {"internal": True}}
    assert cfg["volumes"] == {"pgdata": {}}
    for name, service in cfg["services"].items():
        assert not set(service) & {"ports", "container_name", "env_file", "extends", "network_mode"}
        if name != "db":
            assert service["environment"]["PU_BACKGROUND_EXECUTION"] == "durable"
            assert service["environment"]["GMAIL_AUTO_SYNC_ENABLED"] == "false"
            assert not any(k.endswith(("API_KEY", "CLIENT_SECRET", "BOT_TOKEN")) for k in service["environment"])


def test_fixture_is_not_in_production_image():
    production = (ROOT / "backend/Dockerfile").read_text()
    assert "queue_ci" not in production and "durable_queue" not in production
    for path in Path(__file__).parent.glob("*.py"):
        ast.parse(path.read_text())


def test_workspace_checks_run_before_workers_and_are_postgres_only():
    runner = (ROOT / "scripts/ci/durable_queue/run.py").read_text()
    assert runner.index('/queue_ci/workspace_checks.py') < runner.index('compose("up", "-d", "worker1"')
    probe = (ROOT / "scripts/ci/durable_queue/workspace_checks.py").read_text()
    assert 'url.get_backend_name() == "postgresql"' in probe
    assert 'url.host == "db" and url.database == "puw_queue_test"' in probe
    assert 'External I/O forbidden' in probe


def test_unconditional_cleanup_precedes_artifact():
    workflow = yaml.safe_load((ROOT / ".github/workflows/durable-queue.yml").read_text())
    steps = workflow["jobs"]["recovery"]["steps"]
    assert steps[-2]["if"] == "always()"
    assert "cleanup.py" in steps[-2]["run"]
    assert steps[-1]["if"] == "always()"
    assert steps[-1]["with"]["path"].splitlines() == [
        "queue-artifacts/protocol.json",
        "queue-artifacts/fallback-cleanup.json",
    ]


def test_runtime_protocol_never_records_command_arguments():
    runner = (ROOT / "scripts/ci/durable_queue/run.py").read_text()
    assert '"command": args' not in runner
    assert '"operation": safe_operation(args)' in runner
