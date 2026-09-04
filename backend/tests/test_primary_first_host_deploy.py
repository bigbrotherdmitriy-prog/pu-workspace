import importlib.util
import base64
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def script(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def primary_source(tmp_path):
    path = tmp_path / ".env.primary"
    path.write_text("\n".join([
        "POSTGRES_PASSWORD=" + "p" * 48,
        "APP_SECRET_KEY=" + "a" * 64,
        "TOKEN_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(b"t" * 32).decode(),
        "BOOTSTRAP_TOKEN=" + "b" * 40,
        "GOOGLE_CLIENT_ID=google-client",
        "GOOGLE_CLIENT_SECRET=google-secret",
        "GOOGLE_REDIRECT_URI=https://example.test/projects/google/callback",
        "TELEGRAM_BOT_TOKEN=telegram-token",
    ]) + "\n", encoding="utf-8")
    return path


def test_primary_runtime_preserves_integrations_and_pins_isolated_resources(tmp_path):
    source = primary_source(tmp_path)
    target = tmp_path / "runtime" / ".env.primary"
    revision = "a" * 40
    script("render_primary_environment").render(
        source,
        target,
        revision,
        "puw-primary-next",
        3020,
        f"app-backend:{revision}",
        "puw-primary-next_primary_data",
        "puw-primary-next-primary",
    )
    values = dict(line.split("=", 1) for line in target.read_text(encoding="utf-8").splitlines())
    assert values["APP_SECRET_KEY"] == "a" * 64
    assert values["GOOGLE_CLIENT_SECRET"] == "google-secret"
    assert values["TELEGRAM_BOT_TOKEN"] == "telegram-token"
    assert values["PU_RELEASE_REVISION"] == revision
    assert values["PRIMARY_PORT"] == "3020"
    assert values["PRIMARY_VOLUME_NAME"] == "puw-primary-next_primary_data"
    assert values["PRIMARY_IMAGE"] == f"app-backend:{revision}"


def test_primary_runtime_rejects_duplicate_secret_keys(tmp_path):
    source = primary_source(tmp_path)
    source.write_text(source.read_text(encoding="utf-8") + "APP_SECRET_KEY=shadowed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate primary env key"):
        script("render_primary_environment").read_env(source)


def test_primary_runtime_refuses_critical_secret_rotation(tmp_path):
    source = primary_source(tmp_path)
    previous = tmp_path / "old" / ".env.primary"
    previous.parent.mkdir()
    previous.write_text(source.read_text(encoding="utf-8").replace("a" * 64, "z" * 64), encoding="utf-8")
    with pytest.raises(ValueError, match="APP_SECRET_KEY rotation"):
        script("render_primary_environment").render(
            source,
            tmp_path / "new" / ".env.primary",
            "a" * 40,
            "puw-primary-next",
            3020,
            "app-backend:candidate",
            "puw-primary-next_primary_data",
            "puw-primary-next-primary",
            previous,
        )


@pytest.mark.parametrize(("project", "port"), [
    ("app", 3020),
    ("puw-staging", 3020),
    ("puw-primary-next", 3000),
    ("puw-primary-next", 3010),
    ("puw-primary-next", 443),
])
def test_primary_runtime_rejects_reused_projects_and_ports(tmp_path, project, port):
    with pytest.raises(ValueError):
        script("render_primary_environment").render(
            primary_source(tmp_path),
            tmp_path / "runtime" / ".env.primary",
            "a" * 40,
            project,
            port,
            "app-backend:candidate",
            "puw-primary-next_primary_data",
            "puw-primary-next-primary",
        )


def compose_model():
    project = "puw-primary-next"
    image = "app-backend:" + "a" * 40
    port = 3020
    volume = f"{project}_primary_data"
    prefix = f"{project}-primary"
    environment = {"DATABASE_URL": "redacted", "PU_RELEASE_REVISION": "a" * 40}
    services = {
        name: {"image": image, "environment": environment.copy(), "networks": {"default": None}}
        for name in ("backend", "worker", "scheduler")
    }
    services["backend"].update({
        "container_name": f"{prefix}-backend",
        "ports": [{"host_ip": "127.0.0.1", "published": "3020", "target": 8000, "protocol": "tcp"}],
    })
    services["db"] = {
        "image": "postgres:16-alpine",
        "container_name": f"{prefix}-db",
        "volumes": [{"type": "volume", "source": "data", "target": "/var/lib/postgresql/data"}],
    }
    return {
        "name": project,
        "services": services,
        "volumes": {"data": {"name": volume}},
        "networks": {"default": {"name": f"{project}_default"}},
    }, project, image, port, volume, prefix


def test_primary_compose_accepts_dedicated_loopback_model():
    model, project, image, port, volume, prefix = compose_model()
    script("validate_primary_compose").validate(model, project, image, port, volume, prefix)


@pytest.mark.parametrize("mutation", ["host", "volume", "image", "host_network", "extra_service", "old_endpoint"])
def test_primary_compose_rejects_isolation_escape(mutation):
    model, project, image, port, volume, prefix = compose_model()
    if mutation == "host":
        model["services"]["backend"]["ports"][0]["host_ip"] = "0.0.0.0"
    elif mutation == "volume":
        model["volumes"]["data"]["name"] = "app_pu_pgdata"
    elif mutation == "image":
        model["services"]["worker"]["image"] = "app-backend:latest"
    elif mutation == "host_network":
        model["services"]["scheduler"]["network_mode"] = "host"
    elif mutation == "extra_service":
        model["services"]["caddy"] = {"image": "caddy:latest"}
    else:
        model["services"]["backend"]["command"] = ["curl", "https://puworkspace.ru"]
    with pytest.raises(ValueError):
        script("validate_primary_compose").validate(model, project, image, port, volume, prefix)


def test_primary_compose_allows_inert_oauth_redirect_for_post_cutover_continuity():
    model, project, image, port, volume, prefix = compose_model()
    model["services"]["backend"]["environment"]["GOOGLE_REDIRECT_URI"] = (
        "https://pu-workspace.duckdns.org/projects/google/callback"
    )
    script("validate_primary_compose").validate(model, project, image, port, volume, prefix)


def test_primary_local_smoke_rejects_public_and_reserved_targets():
    module = script("check_primary_local_smoke")
    with pytest.raises(ValueError, match="loopback"):
        module.run("https://example.test", "a" * 40)
    with pytest.raises(ValueError, match="unsafe"):
        module.run("http://127.0.0.1:3000", "a" * 40)


def test_primary_deploy_has_first_host_guardrails_and_no_public_request():
    deploy = (ROOT / "scripts" / "deploy-primary-first-host.sh").read_text(encoding="utf-8")
    for marker in [
        "new-primary host marker is missing",
        "another primary deployment is already in progress",
        "database volume does not belong to this primary Compose project",
        "existing database volume does not belong to this primary Compose project",
        "initial dump import is forbidden for an existing database volume",
        "pg_dump",
        "pg_restore --exit-on-error",
        "rollback()",
        "schema is not proven compatible",
        "ROLLBACK FAILED",
        "mv -Tf",
        ".pu-primary-release",
        "candidate release must not contain an environment file",
        "com.pu-workspace.primary.revision",
        "previous private runtime environment is missing",
        "check_primary_local_smoke.py",
        "no public host is contacted",
        "umask 077",
    ]:
        assert marker in deploy
    assert "curl " not in deploy
    assert "wget " not in deploy
    assert "pu-workspace.duckdns.org" not in deploy
    assert "puworkspace.ru" not in deploy
    assert "docker compose -p app" not in deploy
    assert "app_pu_pgdata" not in deploy
    assert '--user "$(id -u):$(id -g)"' in deploy
    assert '-v "$RELEASE_DIR:/workspace:ro"' in deploy
    assert "-v /dev/null:/workspace/.env:ro" not in deploy


def test_primary_compose_is_standalone_loopback_only_and_has_no_relay():
    compose = (ROOT / "infra" / "primary" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:${PRIMARY_PORT" in compose
    assert "name: ${PRIMARY_VOLUME_NAME" in compose
    assert "${PRIMARY_IMAGE" in compose
    assert "telegram-relay" not in compose
    assert "network_mode: host" not in compose
    assert "profiles: [cutover]" in compose
    assert "pu-workspace.duckdns.org" not in compose


def test_primary_deploy_shell_syntax():
    try:
        result = subprocess.run(
            ["sh", "-n", str(ROOT / "scripts" / "deploy-primary-first-host.sh")],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        pytest.skip("POSIX shell is unavailable")
    assert result.returncode == 0, result.stderr


def test_primary_compose_json_fixture_is_serializable():
    model, *_ = compose_model()
    assert json.loads(json.dumps(model))["services"]["db"]["container_name"].endswith("-db")
