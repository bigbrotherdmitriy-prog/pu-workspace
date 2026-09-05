import base64
import importlib.util
import os
from pathlib import Path
import subprocess
import tarfile

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


def test_staging_public_hostname_may_use_a_separate_proxy_address():
    def split_resolver(host, *_args, **_kwargs):
        if host == "staging-host.example.test":
            address = "93.184.216.34"
        elif host == "staging.example.test":
            address = "1.1.1.1"
        else:
            address = "37.252.23.204"
        return [(2, 1, 6, "", (address, 0))]

    assert script("validate_staging_settings").validate(
        valid_settings(), resolver=split_resolver,
    ) == valid_settings()


def test_staging_public_hostname_must_not_resolve_to_production_server():
    def resolver(host, *_args, **_kwargs):
        address = "93.184.216.34" if host == "staging-host.example.test" else "37.252.23.204"
        return [(2, 1, 6, "", (address, 0))]

    with pytest.raises(ValueError, match="production host"):
        script("validate_staging_settings").validate(valid_settings(), resolver=resolver)


def test_staging_dns_identity_accepts_one_dedicated_host():
    def resolver(host, *_args, **_kwargs):
        address = "37.252.23.204" if host in script("validate_staging_settings").PRODUCTION_HOSTS else "93.184.216.34"
        return [(2, 1, 6, "", (address, 0))]

    assert script("validate_staging_settings").validate(
        valid_settings(), resolver=resolver,
    ) == valid_settings()


def test_staging_rejects_ipv4_mapped_production_address():
    def resolver(*_args, **_kwargs):
        return [(10, 1, 6, "", ("::ffff:37.252.23.204", 0, 0, 0))]

    with pytest.raises(ValueError, match="production host"):
        script("validate_staging_settings").validate(valid_settings(), resolver=resolver)


@pytest.mark.parametrize(("key", "value"), [
    ("STAGING_USER", "root"),
    ("STAGING_PORT", "+3010"),
    ("STAGING_PORT", "03010"),
    ("STAGING_SSH_PORT", "+22"),
    ("STAGING_SSH_PORT", "022"),
    ("STAGING_PUBLIC_URL", "https://staging.example.test:abc"),
    ("STAGING_PUBLIC_URL", "https://staging.example.test:8443"),
    ("STAGING_PUBLIC_URL", "https://staging.example.test/"),
])
def test_staging_settings_reject_privileged_user_and_noncanonical_ports(key, value):
    values = valid_settings()
    values[key] = value
    with pytest.raises(ValueError):
        script("validate_staging_settings").validate(values)


def test_runtime_env_preserves_secrets_but_forces_isolation(tmp_path):
    source_dir = tmp_path / "shared"
    target_dir = tmp_path / "runtime"
    source_dir.mkdir()
    source = source_dir / ".env.staging"
    source.write_text("\n".join([
        "POSTGRES_PASSWORD=" + "p" * 48,
        "APP_SECRET_KEY=" + "a" * 64,
        "TOKEN_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(b"t" * 32).decode(),
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


def test_runtime_env_accepts_canonical_44_character_fernet_key(tmp_path):
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    assert len(key) == 44
    source = tmp_path / ".env.staging"
    source.write_text("\n".join([
        "POSTGRES_PASSWORD=" + "p" * 48,
        "APP_SECRET_KEY=" + "a" * 64,
        "TOKEN_ENCRYPTION_KEY=" + key,
        "BOOTSTRAP_TOKEN=" + "b" * 40,
        "PU_SMOKE_PASSWORD=" + "s" * 32,
    ]), encoding="utf-8")

    values = script("render_staging_environment").read_env(source)

    assert values["TOKEN_ENCRYPTION_KEY"] == key


def test_runtime_env_rejects_64_character_non_fernet_key(tmp_path):
    key = base64.urlsafe_b64encode(b"k" * 48).decode()
    assert len(key) == 64
    source = tmp_path / ".env.staging"
    source.write_text("\n".join([
        "POSTGRES_PASSWORD=" + "p" * 48,
        "APP_SECRET_KEY=" + "a" * 64,
        "TOKEN_ENCRYPTION_KEY=" + key,
        "BOOTSTRAP_TOKEN=" + "b" * 40,
        "PU_SMOKE_PASSWORD=" + "s" * 32,
    ]), encoding="utf-8")

    with pytest.raises(ValueError, match="canonical Fernet key"):
        script("render_staging_environment").read_env(source)


def test_release_gate_uses_latest_push_run_for_each_required_workflow():
    module = script("wait_for_github_checks")
    runs = [
        {"id": 1, "path": ".github/workflows/docker-smoke.yml", "event": "push", "status": "completed", "conclusion": "failure"},
        {"id": 2, "path": ".github/workflows/docker-smoke.yml", "event": "push", "status": "completed", "conclusion": "success"},
        {"id": 3, "path": ".github/workflows/ci.yml", "event": "push", "status": "completed", "conclusion": "success"},
        {"id": 99, "path": ".github/workflows/ci.yml", "event": "pull_request", "status": "completed", "conclusion": "failure"},
        {"id": 100, "path": ".github/workflows/untrusted.yml", "event": "push", "status": "completed", "conclusion": "success"},
    ]
    docker = ".github/workflows/docker-smoke.yml"
    ci = ".github/workflows/ci.yml"
    security = ".github/workflows/security.yml"
    assert module.evaluate(runs, {docker, ci}) == ("success", [])
    assert module.evaluate(runs, {docker, security}) == ("pending", [security])


def test_release_gate_cannot_be_spoofed_by_a_same_named_job():
    module = script("wait_for_github_checks")
    required = {".github/workflows/docker-smoke.yml"}
    runs = [
        {"id": 10, "path": ".github/workflows/docker-smoke.yml", "event": "push", "name": "Docker smoke", "status": "completed", "conclusion": "failure"},
        {"id": 11, "path": ".github/workflows/untrusted.yml", "event": "push", "name": "Docker smoke", "status": "completed", "conclusion": "success"},
    ]
    assert module.evaluate(runs, required) == ("failed", [".github/workflows/docker-smoke.yml"])


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


def test_staging_gateway_preserves_only_explicit_https_proxy_scheme():
    nginx = (ROOT / "infra" / "ci" / "nginx.conf").read_text(encoding="utf-8")
    assert "map $http_x_forwarded_proto $pu_forwarded_proto" in nginx
    assert "https https;" in nginx
    assert "default $scheme;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $pu_forwarded_proto;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $scheme;" not in nginx


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
    assert "--workflow .github/workflows/staging-preflight.yml" in workflow
    assert "actions: read" in workflow
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
        "staging deployment must not run as root",
        "production footprint detected; use a dedicated staging host",
        "dedicated staging host marker is missing",
        "staging root must belong to the deploy user",
        ".pu-staging-archive.sha256",
        "active staging release files differ from the tested archive",
        "com.pu-workspace.staging.revision",
        "existing database volume does not belong to this staging project",
        "ROLLBACK FAILED: previous staging release could not be started",
        "ROLLBACK FAILED: previous staging release failed loopback smoke",
        "ROLLBACK FAILED: previous staging release failed public smoke",
        "staging rollback verified",
        "--staging-authenticated",
        "public_smoke \"$OLD_REVISION\" \"$OLD_REVISION\"",
        "public_smoke \"$REVISION\" \"$REVISION\"",
        "command -v awk",
    ]:
        assert marker in deploy
    assert "/opt/pu-workspace|/opt/pu-workspace/*" in deploy
    assert "compose up -d --no-build --force-recreate --wait --wait-timeout 180 || true" not in deploy


@pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="requires non-root POSIX staging path semantics",
)
def test_first_staging_deploy_without_current_uses_seed_path(tmp_path):
    root = tmp_path / "staging"
    shared = root / "shared"
    shared.mkdir(parents=True)
    source_env = shared / ".env.staging"
    source_env.write_text("PU_SMOKE_PASSWORD=synthetic-password\n", encoding="utf-8")
    source_env.chmod(0o600)

    project = "puw-staging"
    port = "3010"
    public_url = "https://staging.example.test"
    marker = shared / ".pu-staging-host"
    marker.write_text(
        "\n".join([
            "PU_WORKSPACE_DEDICATED_STAGING=1",
            f"STAGING_PROJECT={project}",
            f"STAGING_PORT={port}",
            f"STAGING_PUBLIC_URL={public_url}",
        ]) + "\n",
        encoding="utf-8",
    )
    marker.chmod(0o600)

    release_source = tmp_path / "release-source"
    scripts = release_source / "scripts"
    scripts.mkdir(parents=True)
    (release_source / "docker-compose.ci.yml").write_text("services: {}\n", encoding="utf-8")
    (release_source / "Dockerfile.ci").write_text("FROM scratch\n", encoding="utf-8")
    (scripts / "render_staging_environment.py").write_text(
        "from pathlib import Path\n"
        "import shutil, sys\n"
        "source = sys.argv[sys.argv.index('--source') + 1]\n"
        "output = sys.argv[sys.argv.index('--output') + 1]\n"
        "Path(output).parent.mkdir(parents=True, exist_ok=True)\n"
        "shutil.copyfile(source, output)\n",
        encoding="utf-8",
    )
    (scripts / "validate_staging_compose.py").write_text(
        "import sys\nsys.stdin.read()\n",
        encoding="utf-8",
    )
    (scripts / "check_ci_smoke.py").write_text(
        "import os, sys\n"
        "with open(os.environ['STAGING_TEST_CALL_LOG'], 'a', encoding='utf-8') as stream:\n"
        "    stream.write(' '.join(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    (scripts / "check_public_smoke.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

    revision = "a" * 40
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for path in release_source.rglob("*"):
            if path.is_file():
                bundle.add(path, arcname=path.relative_to(release_source))

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  compose)\n"
        "    for arg in \"$@\"; do\n"
        "      [ \"$arg\" != version ] || exit 0\n"
        "      if [ \"$arg\" = config ]; then printf '{}\\n'; exit 0; fi\n"
        "      [ \"$arg\" != up ] || exit 0\n"
        "    done\n"
        "    ;;\n"
        "  build) exit 0 ;;\n"
        "  image) printf '%s\\n' \"${3##*:}\"; exit 0 ;;\n"
        "  volume) exit 1 ;;\n"
        "  run) cat >/dev/null; exit 0 ;;\n"
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o700)

    call_log = tmp_path / "smoke-calls.log"
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["STAGING_TEST_CALL_LOG"] = str(call_log)
    result = subprocess.run(
        [
            "sh",
            str(ROOT / "scripts" / "deploy-staging.sh"),
            str(root),
            revision,
            project,
            port,
            public_url,
            str(archive),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "first staging deployment; no existing database to back up" in result.stdout
    assert "current release escapes staging root" not in result.stdout + result.stderr
    assert "--seed" in call_log.read_text(encoding="utf-8")


def test_staging_preflight_validates_real_compose_without_external_secrets():
    workflow = (ROOT / ".github" / "workflows" / "staging-preflight.yml").read_text(encoding="utf-8")
    assert "staging-preflight:" in workflow
    assert "sh -n scripts/deploy-staging.sh" in workflow
    assert "scripts/prepare_test_environment.py" in workflow
    assert "scripts/render_staging_environment.py" in workflow
    assert ".staging-preflight/runtime/.env.staging" in workflow
    assert "scripts/validate_staging_compose.py" in workflow
    assert "secrets." not in workflow
    assert "STAGING_HOST" not in workflow


def test_branch_protection_configuration_includes_staging_preflight():
    module = script("configure_github_checks")
    assert "staging-preflight" in module.CHECKS
