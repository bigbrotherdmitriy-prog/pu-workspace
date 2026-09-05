from types import SimpleNamespace
import json
import subprocess
from pathlib import Path

import pytest
import yaml

import local_engine_acceptance as target


def test_workflow_installs_local_dependencies_and_uploads_only_safe_protocol():
    source = (Path(__file__).resolve().parents[2] / ".github/workflows/v54-pilot-runtime.yml").read_text()
    job = yaml.safe_load(source)["jobs"]["local-engines"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert "services" not in job and "environment" not in job
    steps = job["steps"]
    installs = "\n".join(step.get("run", "") for step in steps)
    for package in ("tesseract-ocr-rus", "tesseract-ocr-eng", "tesseract-ocr-osd", "fonts-dejavu-core"):
        assert package in installs
    assert "python scripts/ci/local_engine_acceptance.py" in installs
    upload = next(step for step in steps if "actions/upload-artifact@" in step.get("uses", ""))
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "local-engine-artifacts/protocol.json"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "secrets." not in str(job)


@pytest.mark.parametrize("code,summary,status", [
    (0, "3 passed in 1.2s", "PASS"),
    (0, "3 passed, 1 warning in 1.2s", "PASS"),
    (0, "2 passed, 1 skipped in 1.2s", "FAIL"),
    (0, "3 passed, 1 deselected in 1.2s", "FAIL"),
    (0, "3 passed, 1 xfailed in 1.2s", "FAIL"),
    (0, "2 passed in 1.2s", "FAIL"),
    (0, "4 passed in 1.2s", "FAIL"),
    (1, "3 passed in 1.2s", "FAIL"),
    (0, "", "FAIL"),
])
def test_requires_exact_no_skip_result(code, summary, status):
    assert target.evaluate(code, summary)["result"] == status


def test_child_environment_disables_external_and_inherited_test_gates(monkeypatch):
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PUW_V54_TEST_DATABASE_URL", "TEST_POSTGRES_DSN"):
        monkeypatch.setenv(key, "unsafe")
    env = target.child_env()
    assert not any(key in env for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PUW_V54_TEST_DATABASE_URL", "TEST_POSTGRES_DSN"))
    assert env["DATABASE_URL"] == "sqlite+pysqlite:///:memory:"
    assert env["OCR_EXTERNAL_VISION_ENABLED"] == "false"


@pytest.mark.parametrize("mode", ["failed", "timeout", "success"])
def test_raw_outputs_never_published(tmp_path, monkeypatch, capsys, mode):
    marker = "synthetic-secret-and-document-content"
    def run(*args, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 900
        if mode == "timeout":
            raise subprocess.TimeoutExpired(marker, 900, output=marker, stderr=marker)
        return SimpleNamespace(returncode=0 if mode == "success" else 1,
                               stdout=marker + "\n3 passed in 1.2s", stderr=marker)
    monkeypatch.setattr(target, "ROOT", tmp_path)
    monkeypatch.setattr(target.subprocess, "run", run)
    assert target.main() == (0 if mode == "success" else 1)
    serialized = (tmp_path / "local-engine-artifacts/protocol.json").read_text()
    assert marker not in serialized + capsys.readouterr().out
    assert json.loads(serialized)["raw_output_published"] is False
