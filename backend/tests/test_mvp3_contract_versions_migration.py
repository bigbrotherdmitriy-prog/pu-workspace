from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]


def _config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def test_contract_version_migration_is_the_single_sequential_head():
    script = ScriptDirectory.from_config(_config())
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a11"]
    revision = script.get_revision(CURRENT_SCHEMA_REVISION)
    assert revision.down_revision == "a54f001c0a10"


def test_contract_version_migration_offline_sql_has_safe_history_table():
    cfg = _config()
    output = StringIO()
    cfg.output_buffer = output
    command.upgrade(cfg, "a54f001c0a10:a54f001c0a11", sql=True)
    sql = output.getvalue().lower()
    for token in (
        "contract_versions", "contract_id", "project_id", "resulting_record_version",
        "snapshot", "changed_fields", "actor_user_id", "uq_contract_version_sequence",
    ):
        assert token in sql
    assert "foreign key(contract_id)" not in sql
