from pathlib import Path
import importlib.util

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_v54_workflow_is_branch_scoped_and_has_safe_artifact():
    path = ROOT / ".github/workflows/v54-pilot-runtime.yml"
    text = path.read_text(encoding="utf8")
    # PyYAML 1.1 may coerce `on`; textual trigger checks are intentional too.
    parsed = yaml.safe_load(text)
    assert parsed["permissions"] == {"contents": "read"}
    assert "branches: ['codex/v54-final-runtime']" in text
    assert "workflow_dispatch:" in text and "pull_request:" not in text
    assert "persist-credentials: false" in text
    assert "postgres:16-alpine" in text and "ports:" not in text
    upload = next(
        step for step in parsed["jobs"]["runtime"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["with"]["path"] == "v54-runtime-artifacts/protocol.json"
    assert "backend/app/react_dist" not in text


def test_runtime_orchestrator_never_publishes_captured_output_or_secrets():
    source = (ROOT / "scripts/ci/v54_pilot_workflow.py").read_text(encoding="utf8")
    assert '"raw_published": False' in source
    assert '"raw_output_published": False' in source
    assert "capture_output=True" in source
    assert "print(result.stdout" not in source and "print(result.stderr" not in source
    assert "DROP DATABASE {}" in source and "pg_terminate_backend" in source
    assert 'HEAD = "a54f001c0a01"' in source


def test_runtime_orchestrator_always_cleans_created_databases(monkeypatch, tmp_path):
    path = ROOT / "scripts/ci/v54_pilot_workflow.py"
    spec = importlib.util.spec_from_file_location("v54_pilot_workflow_cleanup_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    cleanup_calls = []
    written = {}
    monkeypatch.setattr(module.ScriptDirectory, "from_config", lambda _config: type("Heads", (), {"get_heads": lambda self: [module.HEAD]})())
    monkeypatch.setattr(module, "create_databases", lambda: module.CREATED.append(module.DATABASES[0]))
    monkeypatch.setattr(module, "test_env", lambda: {})
    monkeypatch.setattr(module, "base_url", lambda database: f"postgresql+psycopg://test@postgres/{database}")
    monkeypatch.setattr(module, "run_phase", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic_failure")))
    monkeypatch.setattr(module, "cleanup_databases", lambda: cleanup_calls.append(tuple(module.CREATED)))
    monkeypatch.setattr(module, "write_protocol", lambda result, failure, runtime: written.update(result=result, failure=failure, runtime=runtime))

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 1
    assert cleanup_calls == [(module.DATABASES[0],)]
    assert module.CREATED == []
    assert written["result"] == "FAIL"
    assert isinstance(written["failure"], RuntimeError)
