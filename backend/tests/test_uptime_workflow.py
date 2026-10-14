from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ROOT / ".github" / "workflows" / "uptime.yml"
# Staged copy: the automation token cannot write .github/workflows (no `workflow`
# scope), so the operator moves this file there. Test whichever exists.
STAGED = ROOT / "infra" / "github-workflows" / "uptime.yml"
WORKFLOW = ACTIVE if ACTIVE.exists() else STAGED


def load():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def triggers(workflow):
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    return workflow.get("on", workflow.get(True))


def test_uptime_runs_on_schedule_and_manually_never_on_pull_requests():
    on = triggers(load())
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "*/15 * * * *"}]


def test_uptime_has_minimal_permissions():
    workflow = load()
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert "permissions" not in workflow["jobs"]["check"]


def test_uptime_checks_public_ui_and_backend_health_with_retry():
    job = load()["jobs"]["check"]
    targets = job["env"]["TARGETS"].split()
    assert targets == ["https://puworkspace.ru/new/", "https://puworkspace.ru/health"]
    probe = job["steps"][0]["run"]
    assert "sleep 60" in probe and probe.count("probe\n") >= 2


def test_uptime_uses_secrets_only_through_env_and_never_echoes_them():
    job = load()["jobs"]["check"]
    assert job["env"]["TELEGRAM_BOT_TOKEN"] == "${{ secrets.TELEGRAM_BOT_TOKEN }}"
    assert job["env"]["TELEGRAM_CHAT_ID"] == "${{ secrets.TELEGRAM_CHAT_ID }}"
    for step in job["steps"]:
        script = step["run"]
        assert "secrets." not in script
        assert "set -x" not in script and "curl -v" not in script
        for line in script.splitlines():
            if line.strip().startswith("echo"):
                assert "TELEGRAM_BOT_TOKEN}" not in line and "$TELEGRAM_BOT_TOKEN" not in line


def test_uptime_keeps_a_single_outage_issue():
    report = load()["jobs"]["check"]["steps"][1]["run"]
    assert "gh issue list --label outage --state open" in report
    assert "gh issue create --label outage" in report
    assert 'gh issue comment "$issue"' in report
    assert 'gh issue close "$issue"' in report


def test_uptime_step_scripts_are_valid_bash(tmp_path):
    for index, step in enumerate(load()["jobs"]["check"]["steps"]):
        script = tmp_path / f"step{index}.sh"
        script.write_text(step["run"], encoding="utf-8")
        subprocess.run(["bash", "-n", str(script)], check=True)
