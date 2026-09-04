"""Static contract tests for the production Docker smoke workflow."""

from pathlib import Path
import shutil
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/docker-smoke.yml"
WORKFLOW_TEXT = WORKFLOW_PATH.read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
JOB = WORKFLOW["jobs"]["docker-smoke"]
STEPS = {step.get("name"): step for step in JOB["steps"] if step.get("name")}


def test_workflow_runs_on_main_codex_and_pull_requests_with_read_only_permissions():
    triggers = WORKFLOW.get("on", WORKFLOW.get(True))
    assert "main" in triggers["push"]["branches"]
    assert "codex/**" in triggers["push"]["branches"]
    assert "pull_request" in triggers
    assert WORKFLOW["permissions"] == {"contents": "read"}
    assert JOB["timeout-minutes"] <= 30


def test_checkout_is_non_persistent_and_current_major_actions_are_used():
    checkout = JOB["steps"][0]
    assert checkout["uses"] == "actions/checkout@v6"
    assert checkout["with"]["persist-credentials"] is False
    assert any(step.get("uses") == "actions/setup-python@v6" for step in JOB["steps"])
    assert any(step.get("uses") == "actions/upload-artifact@v6" for step in JOB["steps"])


def test_smoke_covers_migration_runtime_browser_restart_and_restore():
    required = {
        "Verify migration head and runtime flow",
        "Verify v5.4 runtime composition",
        "Browser login and navigation",
        "Restart API, workers and scheduler; verify persistent data",
        "Restore backup into a second isolated database and compare data",
    }
    assert required <= set(STEPS)
    assert "alembic heads" in STEPS["Verify migration head and runtime flow"]["run"]
    assert "scripts/check_ci_smoke.py --seed" in STEPS["Verify migration head and runtime flow"]["run"]
    restore = STEPS["Restore backup into a second isolated database and compare data"]["run"]
    assert "pg_dump" in restore and "pg_restore" in restore


def test_cleanup_is_unconditional_and_scoped_to_the_disposable_compose_project():
    cleanup = STEPS["Remove this run's disposable stack"]
    assert cleanup["if"] == "always()"
    assert "down --volumes --remove-orphans" in cleanup["run"]
    assert JOB["env"]["COMPOSE_PROJECT_NAME"].startswith("puw-ci-")
    assert JOB["env"]["COMPOSE_FILE"] == "docker-compose.ci.yml"
    assert JOB["env"]["COMPOSE_ENV_FILES"] == ".env.ci"


def test_workflow_shell_blocks_have_valid_bash_syntax():
    bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    assert Path(bash).is_file(), "Bash is required to validate the Linux workflow"
    for step in JOB["steps"]:
        source = step.get("run")
        if not source:
            continue
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-n"],
            input=source,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, f"{step.get('name')}: {result.stderr}"
