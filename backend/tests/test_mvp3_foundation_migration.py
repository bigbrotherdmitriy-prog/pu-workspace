from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]


def config(output=None):
    value = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    value.set_main_option("script_location", str(BACKEND / "migrations"))
    return value


def test_mvp3_foundation_is_single_sequential_head():
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a11"]
    revision = script.get_revision("a54f001c0a10")
    assert revision.down_revision == "a54f001c0a09"


def test_mvp3_foundation_offline_upgrade_contains_cas_evidence_and_history(monkeypatch):
    output = StringIO()
    cfg = config(output)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_mvp3_offline")
    command.upgrade(cfg, "a54f001c0a09:a54f001c0a10", sql=True)
    sql = output.getvalue()
    for token in ("record_version", "evidence_pins", "obligation_history", "governance_history",
                  "due_time", "timezone", "obligation_id", "task_id"):
        assert token in sql
