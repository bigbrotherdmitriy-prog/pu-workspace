from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]


def _config(output=None):
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_mobile_sync_receipts_are_the_single_current_head():
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [CURRENT_SCHEMA_REVISION] == ["c31a7b9d2e40"]
    assert scripts.get_revision("c31a7b9d2e40").down_revision == "b17c4d2e6f90"


def test_mobile_sync_offline_sql_contains_durable_identity_and_conflicts(monkeypatch):
    output = StringIO()
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_mobile_sync_test",
    )
    command.upgrade(_config(output), "b17c4d2e6f90:c31a7b9d2e40", sql=True)
    sql = output.getvalue()
    for token in (
        "mobile_sync_commands",
        "mobile_sync_conflicts",
        "uq_mobile_sync_command_identity",
        "uq_mobile_sync_conflict_command",
        "client_mutation_id",
        "conflicting_fields",
        "server_record_version",
    ):
        assert token in sql
