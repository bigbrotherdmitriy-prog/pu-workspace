from pathlib import Path
import ast
import yaml

ROOT = Path(__file__).resolve().parents[3]


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


def test_unconditional_cleanup_precedes_artifact():
    workflow = yaml.safe_load((ROOT / ".github/workflows/durable-queue.yml").read_text())
    steps = workflow["jobs"]["recovery"]["steps"]
    assert steps[-2]["if"] == "always()"
    assert "cleanup.py" in steps[-2]["run"]
    assert steps[-1]["if"] == "always()"
    assert steps[-1]["with"]["path"] == "queue-artifacts/*.json"
