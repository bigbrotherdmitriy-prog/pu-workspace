"""Failure diagnostics must remain useful without publishing subprocess content."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("stderr,category", [
    (b"ERROR: failed to read dockerfile: open Dockerfile: no such file or directory", "dockerfile_missing"),
    (b"toomanyrequests: registry download limit", "registry_rate_limit"),
    (b"Cannot connect to the Docker daemon", "daemon_unavailable"),
    (b"unexpected undocumented failure", "unclassified"),
])
def test_failed_build_has_safe_diagnostic_and_still_cleans_up(tmp_path, monkeypatch, stderr, category):
    spec = importlib.util.spec_from_file_location("queue_runtime_review", Path(__file__).with_name("run.py"))
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(args, 1, b"DOCUMENT_BODY", stderr + b" password=synthetic-secret")
        return subprocess.CompletedProcess(args, 0, b"a" * 40 if args[:2] == ["git", "rev-parse"] else b"", b"")

    monkeypatch.setattr(runtime.subprocess, "run", command)
    with pytest.raises(RuntimeError):
        runtime.main()
    event = next(row for row in runtime.EVENTS if row.get("exit") == 1)
    assert event["failure"]["category"] == category
    assert event["failure"]["stderr_bytes"] == len(stderr + b" password=synthetic-secret")
    serialized = json.dumps(runtime.EVENTS)
    assert "DOCUMENT_BODY" not in serialized and "synthetic-secret" not in serialized
    assert runtime.EVENTS[-1]["cleanup"] == "PASS"
    assert any("down" in args and "--volumes" in args for args in calls)
    assert not (tmp_path / "queue-runtime-state.json").exists()
