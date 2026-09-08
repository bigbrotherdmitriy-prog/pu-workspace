import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def capacity():
    spec = importlib.util.spec_from_file_location("v7_capacity_test", ROOT / "scripts/ci/v7_ci_capacity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_is_parallel_and_gate_requires_every_job():
    jobs = yaml.safe_load((ROOT / ".github/workflows/v54-pilot-runtime.yml").read_text())["jobs"]
    offline = jobs["backend-offline"]
    assert "services" not in offline and "needs" not in offline
    assert "needs" not in jobs["runtime"]
    gate = jobs["acceptance-gate"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"runtime", "backend-offline", "local-engines", "lint"}
    assert "backend_full" not in (ROOT / "scripts/ci/v54_pilot_workflow.py").read_text()
    for job in (offline, gate):
        upload = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
        assert upload["if"] == "always()"
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["path"].endswith("/protocol.json")
    assert "codex/v7-execution-wave3" in (ROOT / ".github/workflows/v54-pilot-runtime.yml").read_text()


def test_offline_environment_drops_external_credentials_and_pg(monkeypatch):
    module = capacity()
    for key in ("DATABASE_URL", "TEST_POSTGRES_DSN", "PUW_MVP4_TEST_DATABASE_URL",
                "GOOGLE_CLIENT_SECRET", "OPENAI_API_KEY", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
                "POSTGRES_PASSWORD", "APP_SECRET_KEY", "TOKEN_ENCRYPTION_KEY"):
        monkeypatch.setenv(key, "synthetic-do-not-inherit")
    env = module.offline_env()
    assert "synthetic-do-not-inherit" not in env.values()
    assert env["DATABASE_URL"] == "sqlite+pysqlite:///:memory:"
    assert env["PU_TEST_POSTGRES"] == "0"
    assert env["OCR_EXTERNAL_VISION_ENABLED"] == "false"


@pytest.mark.parametrize("code,output,expected", [
    (0, "2207 passed, 46 skipped, 35 warnings in 592.09s\n", "PASS"),
    (0, "", "FAIL"), (0, "46 skipped in 1s\n", "FAIL"),
    (0, "2 passed, 1 xfailed in 1s\n", "FAIL"),
    (1, "2 passed in 1s\n", "FAIL"),
])
def test_offline_summary_is_safe_and_not_postgres_acceptance(code, output, expected):
    result = capacity().evaluate_offline(code, output + "synthetic-document-secret\n")
    assert result["result"] == expected
    assert result["postgres_runtime"] == "NOT_RUN"
    assert result["raw_output_published"] is False
    assert "synthetic-document-secret" not in json.dumps(result)


@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", None])
def test_aggregate_missing_or_unsuccessful_job_fails(state):
    module = capacity()
    needs = {key: {"result": "success"} for key in module.REQUIRED_JOBS}
    if state is None:
        needs.pop("backend-offline")
    else:
        needs["backend-offline"]["result"] = state
    assert module.evaluate_gate(needs)["result"] == "FAIL"
    assert module.evaluate_gate({key: {"result": "success"} for key in module.REQUIRED_JOBS})["result"] == "PASS"


def test_all_37_pg_proofs_and_a20_are_preserved():
    from test_mvp_runtime_coverage import runner
    module = runner()
    assert sum(map(len, module.PINNED_POSTGRES_TESTS.values())) == 37
    assert module.HEAD == "a54f001c0a20"
    assert module.RUNTIME_BUDGET_SECONDS == 1320
    assert module.CLEANUP_RESERVE_SECONDS == 60
    env = capacity().offline_env()
    assert not any(key in env for key in module.TEST_DATABASE_KEYS)


@pytest.mark.parametrize("fail", [False, True])
def test_offline_real_entrypoint_captures_only_safe_counters(monkeypatch, tmp_path, capsys, fail):
    module = capacity()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if fail:
            raise module.subprocess.TimeoutExpired(args, 900, output="synthetic-document", stderr="synthetic-key")
        return SimpleNamespace(returncode=0, stdout="2 passed, 1 skipped in 0.3s\n", stderr="synthetic-key")
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.run_offline() == (1 if fail else 0)
    args, options = calls[0]
    assert "backend/tests" in args and options["timeout"] == 900
    assert options["capture_output"] is True and options["env"]["PU_TEST_POSTGRES"] == "0"
    protocol = (tmp_path / "v7-backend-offline-artifacts/protocol.json").read_text()
    assert "synthetic-" not in protocol + capsys.readouterr().out
    assert json.loads(protocol)["postgres_runtime"] == "NOT_RUN"


@pytest.mark.parametrize("needs", ["", "not-json-secret", "[]", "null", '{"runtime":{"result":"success"}}'])
def test_gate_entrypoint_missing_and_malformed_results_fail_safely(monkeypatch, tmp_path, needs):
    module = capacity()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv("CI_NEEDS_JSON", needs)
    assert module.run_gate() == 1
    text = (tmp_path / "v7-ci-capacity-artifacts/protocol.json").read_text()
    assert json.loads(text)["result"] == "FAIL" and "secret" not in text


def test_publication_error_is_safe_failure(monkeypatch, tmp_path, capsys):
    module = capacity()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    def fail(*args, **kwargs):
        raise OSError("synthetic-secret-path")
    monkeypatch.setattr(Path, "mkdir", fail)
    assert module.publish(module.evaluate_gate({}), "synthetic") == 1
    assert capsys.readouterr().out == "CI protocol publication: FAIL\n"
