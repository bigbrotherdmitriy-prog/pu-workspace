"""Execute workflow Python blocks with synthetic inputs, without Docker or secrets."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/docker-smoke.yml").read_text(encoding="utf-8"))
STEPS = {step.get("name"): step for step in WORKFLOW["jobs"]["smoke"]["steps"]}


def python_block(step_name):
    source = STEPS[step_name]["run"]
    return source.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def test_runner_context_is_not_used_in_job_environment():
    # GitHub rejected run 33741918971 before allocating any job: runner is
    # available in step env, not jobs.<job_id>.env. YAML parsing alone misses it.
    for job in WORKFLOW["jobs"].values():
        assert not any(re.search(r"\brunner\s*\.", str(value))
                       for value in job.get("env", {}).values())


def test_generated_environment_overrides_stale_shell_values(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "test.env"
    github_env = tmp_path / "github-env"
    monkeypatch.setenv("CI_ENV_FILE", str(env_file))
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.setenv("CI_RAW_LOG", str(tmp_path / "raw.log"))
    monkeypatch.setenv("CI_DIAGNOSTICS", str(tmp_path / "diagnostics"))
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "a" * 40 + "\n")
    exec(compile(python_block("Generate isolated test environment"), "workflow", "exec"), {})
    generated = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    exported = dict(line.split("=", 1) for line in github_env.read_text().splitlines())
    for key in ("CI_ENV_FILE", "CI_RAW_LOG", "CI_DIAGNOSTICS"):
        assert "runner.temp" in STEPS["Generate isolated test environment"]["env"][key]
        assert exported[key] == os.environ[key], f"Later steps cannot access {key}"
    for key in ("POSTGRES_PASSWORD", "APP_SECRET_KEY", "BOOTSTRAP_TOKEN", "TOKEN_ENCRYPTION_KEY", "PU_RELEASE_REVISION", "CI_SMOKE_PASSWORD"):
        assert exported.get(key) == generated[key], f"Inherited {key} can override the test env file"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", generated["POSTGRES_PASSWORD"])
    # Generated secrets may only be emitted as GitHub masking directives.
    assert all(line.startswith("::add-mask::") for line in capsys.readouterr().out.splitlines())


def test_diagnostics_keep_ordered_safe_log_evidence(tmp_path, monkeypatch):
    output = tmp_path / "diagnostics"
    raw = tmp_path / "raw.log"
    marker = "SYNTHETIC-DO-NOT-PUBLISH"
    raw.write_text(f"FAIL step=bootstrap http=403 reason={marker}\n", encoding="utf-8")
    for key, value in {"CI_DIAGNOSTICS": output, "CI_RAW_LOG": raw, "CI_ENV_FILE": tmp_path / "env", "CI_COMPOSE_PROJECT": "puw-ci-1-1"}.items():
        monkeypatch.setenv(key, str(value))

    def collect(args, **kwargs):
        if "ps" in args:
            data = json.dumps([{"Service": "backend", "State": "exited", "ExitCode": 1, "Command": marker}])
        else:
            data = f"backend | Traceback {marker}\nbackend | OperationalError {marker}\ndb | database system is ready to accept connections\n"
        return subprocess.CompletedProcess(args, 0, data, marker)

    monkeypatch.setattr(subprocess, "run", collect)
    exec(compile(python_block("Collect sanitized diagnostics before cleanup"), "workflow", "exec"), {})
    summary = json.loads((output / "diagnostics.json").read_text())
    assert summary["containers"][0]["exit_code"] == 1
    safe_log = (output / "compose.sanitized.log").read_text()
    assert "Traceback" in safe_log and "OperationalError" in safe_log
    assert "step=bootstrap http=403" in safe_log
    assert marker not in "\n".join(p.read_text() for p in output.iterdir())


def test_every_compose_command_has_explicit_isolation_and_cleanup_is_unconditional():
    for step in STEPS.values():
        for line in step.get("run", "").splitlines():
            if "docker compose " in line:
                for arg in ('--project-name "$CI_COMPOSE_PROJECT"', '--file docker-compose.ci.yml', '--env-file "$CI_ENV_FILE"'):
                    assert arg in line
    cleanup = STEPS["Clean up isolated Compose project"]
    assert cleanup["if"] == "always()"
    assert "down --volumes --remove-orphans" in cleanup["run"]
    names = list(STEPS)
    assert names.index("Collect sanitized diagnostics before cleanup") < names.index("Clean up isolated Compose project")


def bash_executable():
    binary = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    assert Path(binary).is_file(), "Bash is required to validate the Linux CI workflow"
    return binary


def test_workflow_shell_and_embedded_python_syntax():
    for step in STEPS.values():
        if "run" not in step:
            continue
        result = subprocess.run([bash_executable(), "--noprofile", "--norc", "-n"],
                                input=step["run"], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        if "<<'PY'" in step["run"]:
            compile(python_block(step["name"]), step["name"], "exec")


@pytest.mark.parametrize("down_exit", [0, 9])
def test_cleanup_targets_only_test_project_and_preserves_down_failure(tmp_path, down_exit):
    # Mock docker/timeout only. Exercise the actual shell trap and file cleanup
    # on files created by this test; this is not a real Compose teardown test.
    env_file, raw_log, command_log = (tmp_path / name for name in ("test.env", "raw.log", "commands"))
    env_file.write_text("synthetic\n")
    raw_log.write_text("synthetic\n")
    (tmp_path / "docker-compose.ci.yml").write_text("services: {}\n")
    environment = dict(os.environ, CI_ENV_FILE=env_file.as_posix(), CI_RAW_LOG=raw_log.as_posix(),
                       CI_COMMAND_LOG=command_log.as_posix(), CI_COMPOSE_PROJECT="puw-ci-123-4",
                       CI_DOWN_EXIT=str(down_exit))
    prelude = '''
docker() { printf '%s\\n' "$@" >> "$CI_COMMAND_LOG"; return "$CI_DOWN_EXIT"; }
timeout() { shift; "$@"; }
'''
    result = subprocess.run([bash_executable(), "--noprofile", "--norc", "-e", "-c",
                             prelude + STEPS["Clean up isolated Compose project"]["run"]],
                            cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == down_exit, result.stderr
    assert command_log.read_text().splitlines() == [
        "compose", "--project-name", "puw-ci-123-4", "--file", "docker-compose.ci.yml",
        "--env-file", env_file.as_posix(), "down", "--volumes", "--remove-orphans", "--timeout", "20",
    ]
    assert not env_file.exists() and not raw_log.exists()


def test_fault_mode_is_opt_in_and_does_not_hide_failure():
    mode = WORKFLOW['jobs']['smoke']['env']['CI_FAULT_MODE']
    assert "github.event_name == 'push'" in mode
    assert "github.ref == 'refs/heads/codex/ci-smoke-integration'" in mode
    assert "contains(github.event.head_commit.message, '[ci-smoke-fault]')" in mode
    assert STEPS['Inject bootstrap conflict on initialized test database']['if'] == "env.CI_FAULT_MODE == 'true'"
    assert all(not step.get('continue-on-error') for step in STEPS.values())
    for name in ('Verify exact injected failure', 'Verify test resources are absent after cleanup', 'Upload cleanup verification'):
        assert 'always()' in STEPS[name]['if']
    names = list(STEPS)
    assert names.index('Run authenticated API smoke inside backend') < names.index('Inject bootstrap conflict on initialized test database')
    assert names.index('Clean up isolated Compose project') < names.index('Verify test resources are absent after cleanup')
    assert STEPS['Upload sanitized diagnostics']['with']['if-no-files-found'] == 'error'


@pytest.mark.parametrize('probe_exit', [0, 1, 17])
def test_fault_probe_preserves_actual_exit_code(tmp_path, probe_exit):
    script = tmp_path / 'scripts' / 'ci' / 'smoke_api.py'
    script.parent.mkdir(parents=True)
    script.write_text('# synthetic stdin\n')
    output, raw = tmp_path / 'outputs', tmp_path / 'raw.log'
    environment = dict(os.environ, CI_COMPOSE_PROJECT='puw-ci-123-4',
                       CI_ENV_FILE=(tmp_path / 'env').as_posix(), CI_RAW_LOG=raw.as_posix(),
                       GITHUB_OUTPUT=output.as_posix(), CI_PROBE_EXIT=str(probe_exit))
    prelude = 'docker() { return "$CI_PROBE_EXIT"; }\n'
    result = subprocess.run([bash_executable(), '--noprofile', '--norc', '-e', '-c',
                             prelude + STEPS['Inject bootstrap conflict on initialized test database']['run']],
                            cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == probe_exit, result.stderr
    assert output.read_text().strip() == f'exit_code={probe_exit}'


@pytest.mark.parametrize('outcome,code,lines,expected', [
    ('failure', '1', ['FAIL step=bootstrap http=409 reason=expected HTTP 200'], True),
    ('success', '0', [], False),
    ('failure', '1', ['FAIL step=bootstrap http=403 reason=expected HTTP 200'], False),
    ('failure', '17', ['FAIL step=bootstrap http=409 reason=expected HTTP 200'], False),
    ('failure', '1', ['FAIL step=bootstrap http=409 reason=expected HTTP 200'] * 2, False),
    ('failure', '', [], False),
])
def test_fault_assertion_accepts_only_expected_bootstrap_conflict(tmp_path, monkeypatch, outcome, code, lines, expected):
    raw = tmp_path / 'raw.log'
    raw.write_text('PASS smoke-api\n' + '\n'.join(lines))
    output = tmp_path / 'diagnostics'
    for key, value in {'CI_RAW_LOG': str(raw), 'CI_DIAGNOSTICS': str(output),
                       'CI_FAULT_OUTCOME': outcome, 'CI_FAULT_EXIT': code}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit) as exc:
        exec(compile(python_block('Verify exact injected failure'), 'workflow', 'exec'), {})
    assert exc.value.code == (0 if expected else 1)
    assert json.loads((output / 'fault-assertion.json').read_text())['expected_bootstrap_conflict_confirmed'] is expected


@pytest.mark.parametrize('fault', [None, 'leftover', 'daemon_error', 'timeout', 'tempfile'])
def test_cleanup_verification_checks_all_project_labels_and_never_deletes(tmp_path, monkeypatch, fault):
    output = tmp_path / 'diagnostics'
    env_file, raw = tmp_path / 'test.env', tmp_path / 'raw.log'
    if fault == 'tempfile':
        env_file.write_text('SYNTHETIC-SECRET')
    for key, value in {'CI_COMPOSE_PROJECT': 'puw-ci-123-4', 'CI_DIAGNOSTICS': str(output),
                       'CI_ENV_FILE': str(env_file), 'CI_RAW_LOG': str(raw)}.items():
        monkeypatch.setenv(key, value)
    calls = []
    def docker(args, **kwargs):
        calls.append(args)
        assert kwargs['timeout'] == 20
        if 'volume' in args:
            if fault == 'daemon_error':
                return subprocess.CompletedProcess(args, 1, '', 'SYNTHETIC-SECRET')
            if fault == 'timeout':
                raise subprocess.TimeoutExpired(args, 20)
            if fault == 'leftover':
                return subprocess.CompletedProcess(args, 0, 'SYNTHETIC-RESOURCE\n', '')
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', docker)
    with pytest.raises(SystemExit) as exc:
        exec(compile(python_block('Verify test resources are absent after cleanup'), 'workflow', 'exec'), {})
    assert exc.value.code == (0 if fault is None else 1)
    assert calls == [
        ['docker', 'ps', '--all', '--quiet', '--filter', 'label=com.docker.compose.project=puw-ci-123-4'],
        ['docker', 'network', 'ls', '--quiet', '--filter', 'label=com.docker.compose.project=puw-ci-123-4'],
        ['docker', 'volume', 'ls', '--quiet', '--filter', 'label=com.docker.compose.project=puw-ci-123-4'],
    ]
    report = (output / 'cleanup-verification.json').read_text()
    assert 'SYNTHETIC' not in report
    assert json.loads(report)['cleanup_confirmed'] is (fault is None)
