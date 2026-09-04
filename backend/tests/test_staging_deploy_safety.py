import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def script(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_settings():
    return {
        "STAGING_HOST": "staging-host.example.test",
        "STAGING_USER": "puw_staging",
        "STAGING_ROOT": "/opt/pu-workspace-staging",
        "STAGING_PROJECT": "puw-staging",
        "STAGING_PORT": "3010",
        "STAGING_PUBLIC_URL": "https://staging.example.test",
        "STAGING_SSH_PORT": "22",
    }


@pytest.mark.parametrize(("key", "value"), [
    ("STAGING_HOST", "pu-workspace.duckdns.org"),
    ("STAGING_HOST", "127.0.0.1"),
    ("STAGING_ROOT", "/opt/pu-workspace"),
    ("STAGING_ROOT", "/opt/pu-workspace/staging"),
    ("STAGING_PROJECT", "app"),
    ("STAGING_PORT", "3000"),
    ("STAGING_PUBLIC_URL", "https://puworkspace.ru"),
    ("STAGING_PUBLIC_URL", "http://staging.example.test"),
    ("STAGING_PUBLIC_URL", "https://127.0.0.1"),
])
def test_staging_settings_reject_production_and_unsafe_targets(key, value):
    values = valid_settings()
    values[key] = value
    with pytest.raises(ValueError):
        script("validate_staging_settings").validate(values)


def test_staging_settings_accept_isolated_target():
    assert script("validate_staging_settings").validate(valid_settings()) == valid_settings()


def test_runtime_env_preserves_secrets_but_forces_isolation(tmp_path):
    source_dir = tmp_path / "shared"
    target_dir = tmp_path / "runtime"
    source_dir.mkdir()
    source = source_dir / ".env.staging"
    source.write_text("\n".join([
        "POSTGRES_PASSWORD=" + "p" * 48,
        "APP_SECRET_KEY=" + "a" * 64,
        "TOKEN_ENCRYPTION_KEY=" + "t" * 44 + "=",
        "BOOTSTRAP_TOKEN=" + "b" * 40,
        "PU_SMOKE_PASSWORD=" + "s" * 32,
        "GOOGLE_CLIENT_ID=must-be-removed",
        "GMAIL_AUTO_SYNC_ENABLED=true",
    ]), encoding="utf-8")
    target = target_dir / ".env.staging"
    revision = "a" * 40
    module = script("render_staging_environment")
    module.render(source, target, revision, 3010)
    values = dict(line.split("=", 1) for line in target.read_text(encoding="utf-8").splitlines())
    assert values["POSTGRES_PASSWORD"] == "p" * 48
    assert values["PU_RELEASE_REVISION"] == revision
    assert values["PU_TEST_PORT"] == "3010"
    assert values["PU_TEST_IMAGE_REPOSITORY"] == "pu-workspace-staging"
    assert values["GOOGLE_CLIENT_ID"] == ""
    assert values["GMAIL_AUTO_SYNC_ENABLED"] == "false"


def test_release_gate_uses_latest_run_for_each_required_check():
    module = script("wait_for_github_checks")
    runs = [
        {"id": 1, "name": "docker-smoke", "status": "completed", "conclusion": "failure"},
        {"id": 2, "name": "docker-smoke", "status": "completed", "conclusion": "success"},
        {"id": 3, "name": "test-and-build", "status": "completed", "conclusion": "success"},
    ]
    assert module.evaluate(runs, {"docker-smoke", "test-and-build"}) == ("success", [])
    assert module.evaluate(runs, {"docker-smoke", "security"}) == ("pending", ["security"])


def test_staging_workflow_is_opt_in_serial_and_does_not_target_production():
    workflow = (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text(encoding="utf-8")
    assert "vars.STAGING_ENABLED == 'true'" in workflow
    assert "group: pu-workspace-public-staging" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "pu-workspace.duckdns.org" not in workflow
    assert "deploy-production.sh" not in workflow


def test_staging_deploy_script_has_lock_backup_rollback_and_public_smoke():
    deploy = (ROOT / "scripts" / "deploy-staging.sh").read_text(encoding="utf-8")
    for marker in [
        "flock -n", "pg_dump", "pg_restore", "rollback()", "check_public_smoke.py",
        "staging root must be an existing canonical non-symlink path",
        "release archive must not contain links",
    ]:
        assert marker in deploy
    assert "/opt/pu-workspace|/opt/pu-workspace/*" in deploy
