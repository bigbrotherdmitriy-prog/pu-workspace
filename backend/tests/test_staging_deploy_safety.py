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
    ("STAGING_HOST", "37.252.23.204"),
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


def test_staging_hostname_must_not_resolve_to_production_server():
    def production_resolver(*_args, **_kwargs):
        return [(2, 1, 6, "", ("37.252.23.204", 0))]

    with pytest.raises(ValueError, match="production host"):
        script("validate_staging_settings").validate(valid_settings(), resolver=production_resolver)


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
        {"id": 1, "name": "docker-smoke", "status": "completed", "conclusion": "failure", "app": {"slug": "github-actions"}},
        {"id": 2, "name": "docker-smoke", "status": "completed", "conclusion": "success", "app": {"slug": "github-actions"}},
        {"id": 3, "name": "test-and-build", "status": "completed", "conclusion": "success", "app": {"slug": "github-actions"}},
        {"id": 99, "name": "test-and-build", "status": "completed", "conclusion": "failure", "app": {"slug": "untrusted-app"}},
    ]
    assert module.evaluate(runs, {"docker-smoke", "test-and-build"}) == ("success", [])
    assert module.evaluate(runs, {"docker-smoke", "security"}) == ("pending", ["security"])


def compose_model(tmp_path):
    revision = "a" * 40
    project = "puw-staging"
    release = tmp_path / "release"
    nginx = release / "infra" / "ci" / "nginx.conf"
    nginx.parent.mkdir(parents=True)
    nginx.write_text("server {}", encoding="utf-8")
    disabled = {
        "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "", "GOOGLE_REDIRECT_URI": "",
        "TELEGRAM_BOT_TOKEN": "", "GEMINI_API_KEY": "", "YANDEX_CLIENT_ID": "",
        "YANDEX_CLIENT_SECRET": "", "GMAIL_AUTO_SYNC_ENABLED": "false",
        "AI_SECRETARY_AUTOMATION_ENABLED": "false",
    }
    services = {
        name: {"image": f"pu-workspace-staging:{revision}", "environment": disabled.copy(), "networks": {"default": None}}
        for name in ("backend", "migrate", "worker", "scheduler")
    }
    services.update({
        "db": {
            "image": "postgres:16-alpine", "networks": {"default": None},
            "volumes": [{"type": "volume", "source": "data", "target": "/var/lib/postgresql/data"}],
        },
        "gateway": {
            "image": "nginx:1.28-alpine", "networks": {"default": None, "edge": None},
            "ports": [{"host_ip": "127.0.0.1", "published": "3010", "target": 8080, "protocol": "tcp"}],
            "volumes": [{
                "type": "bind", "source": str(nginx.resolve()),
                "target": "/etc/nginx/conf.d/default.conf", "read_only": True,
            }],
        },
    })
    model = {
        "name": project,
        "services": services,
        "volumes": {"data": {"name": f"{project}_data"}},
        "networks": {
            "default": {"name": f"{project}_default", "internal": True},
            "edge": {"name": f"{project}_edge"},
        },
    }
    return model, project, revision, release


def test_rendered_compose_accepts_only_isolated_model(tmp_path):
    model, project, revision, release = compose_model(tmp_path)
    script("validate_staging_compose").validate(model, project, revision, 3010, release)


@pytest.mark.parametrize("mutation", ["host_port", "volume", "privileged", "network", "credential", "mount_escape"])
def test_rendered_compose_rejects_escape_paths(tmp_path, mutation):
    model, project, revision, release = compose_model(tmp_path)
    if mutation == "host_port":
        model["services"]["gateway"]["ports"][0]["host_ip"] = "0.0.0.0"
    elif mutation == "volume":
        model["volumes"]["data"]["name"] = "pu_pgdata"
    elif mutation == "privileged":
        model["services"]["backend"]["privileged"] = True
    elif mutation == "network":
        model["networks"]["edge"]["external"] = True
    elif mutation == "credential":
        model["services"]["worker"]["environment"]["GOOGLE_CLIENT_SECRET"] = "not-isolated"
    else:
        outside = tmp_path / "outside.conf"
        outside.write_text("server {}", encoding="utf-8")
        nginx = release / "infra" / "ci" / "nginx.conf"
        nginx.unlink()
        try:
            nginx.symlink_to(outside)
        except OSError:
            pytest.skip("symlinks are unavailable")
        model["services"]["gateway"]["volumes"][0]["source"] = str(outside.resolve())
    with pytest.raises(ValueError):
        script("validate_staging_compose").validate(model, project, revision, 3010, release)


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
        "release archive must not contain links", "umask 077", "mode must be 600 or 400",
        "database revision is unknown; refusing unproven application rollback",
        "current release escapes staging root", "previous files kept at",
    ]:
        assert marker in deploy
    assert "/opt/pu-workspace|/opt/pu-workspace/*" in deploy
