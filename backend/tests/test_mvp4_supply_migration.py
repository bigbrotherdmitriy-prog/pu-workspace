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
    config.set_main_option("sqlalchemy.url", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_test")
    return config


def test_supply_schema_is_single_sequential_head():
    script = ScriptDirectory.from_config(_config())
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a17"]
    assert script.get_revision("a54f001c0a16").down_revision == "a54f001c0a15"


def test_supply_migration_contains_exact_evidence_and_immutable_history():
    output = StringIO()
    command.upgrade(_config(output), "a54f001c0a15:a54f001c0a16", sql=True)
    sql = output.getvalue()
    for table in ("mvp4_supply_cases", "mvp4_supply_case_versions", "mvp4_supply_command_receipts"):
        assert f"CREATE TABLE {table}" in sql
    for constraint in (
        "fk_mvp4_supply_exact_evidence",
        "uq_mvp4_supply_request_key",
        "uq_mvp4_supply_version_sequence",
        "uq_mvp4_supply_command_key",
        "ck_mvp4_supply_no_external_action",
    ):
        assert constraint in sql
    assert "v54_evidence" in sql
    assert "source_version_id" in sql
    assert "external_action_status = 'not_created'" in sql
