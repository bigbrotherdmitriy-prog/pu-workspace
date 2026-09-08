"""Offline SQL checks are not PostgreSQL upgrade/downgrade execution."""
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


def config(output):
    root = Path(__file__).resolve().parents[1]
    value = Config(str(root / "alembic.ini"), output_buffer=output)
    value.set_main_option("script_location", str(root / "migrations"))
    value.set_main_option("sqlalchemy.url", "postgresql+psycopg://synthetic@localhost/puw_mvp4_test_graph")
    return value


def test_graph_migration_is_single_new_successor():
    scripts = ScriptDirectory.from_config(config(StringIO()))
    assert scripts.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a21"]
    assert scripts.get_revision("a54f001c0a20").down_revision == "a54f001c0a19"


def test_upgrade_sql_adds_nullable_intent_without_backfill(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    output = StringIO()
    command.upgrade(config(output), "a54f001c0a19:a54f001c0a20", sql=True)
    sql = output.getvalue()
    assert "ADD COLUMN graph_revision BIGINT DEFAULT '1' NOT NULL" in sql
    assert "ADD COLUMN duration_days INTEGER;" in sql
    assert "ADD COLUMN is_milestone BOOLEAN;" in sql
    assert "UPDATE schedule_items" not in sql and "UPDATE schedule_baselines" not in sql
    assert "duration_days IS NOT NULL AND is_milestone IS NOT NULL" in sql
    assert "constraint_type IS NOT NULL" in sql


def test_downgrade_locks_before_intent_check_and_column_drop(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    output = StringIO()
    command.downgrade(config(output), "a54f001c0a20:a54f001c0a19", sql=True)
    sql = output.getvalue()
    lock = sql.index("LOCK TABLE schedule_baselines, schedule_items IN ACCESS EXCLUSIVE MODE")
    guard = sql.index("DO $$ BEGIN IF EXISTS")
    drop = sql.index("DROP COLUMN")
    assert lock < guard < drop
    for field in ("project_start", "duration_days", "is_milestone", "predecessor_ids",
                  "constraint_type", "constraint_date", "not_before_date"):
        assert f"{field} IS NOT NULL" in sql
    assert "schedule_graph_downgrade_requires_verified_restore" in sql
