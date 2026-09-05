"""Schema regression for additive exact-time DeadlineClaim storage."""
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]


def migration_config(output=None):
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_a09_remains_in_single_head_chain_and_adds_nullable_time(monkeypatch):
    output = StringIO()
    config = migration_config(output)
    assert ScriptDirectory.from_config(config).get_heads() == [CURRENT_SCHEMA_REVISION]
    assert CURRENT_SCHEMA_REVISION == "a54f001c0a15"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline",
    )
    command.upgrade(config, "a54f001c0a08:a54f001c0a09", sql=True)
    rendered = output.getvalue()
    assert "ADD COLUMN due_time TIME WITHOUT TIME ZONE" in rendered
    assert "NOT NULL" not in rendered


def test_a09_offline_downgrade_refuses_silent_time_loss(monkeypatch):
    output = StringIO()
    config = migration_config(output)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline",
    )
    command.downgrade(config, "a54f001c0a09:a54f001c0a08", sql=True)
    rendered = output.getvalue()
    guard = "Timed deadline claims require explicit archival before downgrade"
    assert guard in rendered
    assert rendered.index(guard) < rendered.index("DROP COLUMN due_time")
