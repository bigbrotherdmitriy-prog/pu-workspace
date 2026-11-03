"""Timestamp type must match the existing historical PostgreSQL migration."""
import importlib.util
from pathlib import Path
from sqlalchemy.dialects import postgresql
from app.models.ai_policy import ProjectAIPolicy


def test_ai_policy_timestamp_matches_historical_migration(monkeypatch):
    migration = Path(__file__).resolve().parents[1] / 'migrations/versions/b31d8a7f4c02_add_project_ai_policy.py'
    spec = importlib.util.spec_from_file_location('historical_ai_policy', migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = {}
    def capture(name, *columns):
        captured[name] = {c.name:c for c in columns}
    monkeypatch.setattr(module.op, 'create_table', capture)
    module.upgrade()  # Captures the existing DDL contract, no DB connection.
    old = captured['project_ai_policies']['updated_at']
    current = ProjectAIPolicy.__table__.c.updated_at
    assert old.type.timezone is True
    assert current.type.compile(dialect=postgresql.dialect()) == old.type.compile(dialect=postgresql.dialect())
    assert current.server_default is not None and current.onupdate is not None
