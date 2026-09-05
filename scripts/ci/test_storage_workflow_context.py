"""GitHub context availability: dynamic service ports are step-scoped."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_storage_service_connection_is_configured_in_step_not_job_env():
    workflow = yaml.safe_load((ROOT / ".github/workflows/storage-mutation-runtime.yml").read_text(encoding="utf8"))
    job = workflow["jobs"]["postgres-worker-and-browser"]
    assert all("job.services" not in str(value) for value in job.get("env", {}).values())
    steps = job["steps"]
    setup = next(step for step in steps if step.get("name") == "Configure isolated PostgreSQL connection")
    assert setup["env"]["CI_POSTGRES_PORT"] == "${{ job.services.postgres.ports['5432'] }}"
    assert 'os.environ["GITHUB_ENV"]' in setup["run"]
    assert "TEST_POSTGRES_DSN" in setup["run"] and "DATABASE_URL" in setup["run"]
    assert steps.index(setup) < next(i for i, step in enumerate(steps) if "migrate" in step.get("name", ""))
