"""Orchestrator contract only: PostgreSQL execution is a separate CI gate."""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_history_database_is_owned_scoped_and_migrated_before_acceptance(monkeypatch):
    path = ROOT / "scripts/ci/v54_pilot_workflow.py"
    spec = importlib.util.spec_from_file_location("mvp2_runtime_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "base_url", lambda name: "synthetic-db:" + name)
    env = module.test_env()
    name = "puw_mvp2_test_gmail_history"
    assert name in module.DATABASES
    assert env["PUW_MVP2_GMAIL_HISTORY_DATABASE_URL"] == "synthetic-db:" + name
    assert env["GMAIL_AUTO_SYNC_ENABLED"] == "false"
    source = path.read_text(encoding="utf8")
    assert source.index('run_phase("gmail_history_migration"') < source.index('run_phase("postgres_gmail_history"')
    assert '"backend/tests/test_mvp2_gmail_history_cursor_postgres.py"' in source
    assert '"backend/tests/test_mvp2_gmail_history_migration.py"' in source
    assert 'PUW_MVP2_GMAIL_HISTORY_DATABASE_URL=""' in source
    assert 'for name in reversed(CREATED)' in source
    assert '"raw_output_published": False' in source
