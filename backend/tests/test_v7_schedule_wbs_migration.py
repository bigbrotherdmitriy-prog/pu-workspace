from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


def config(output=None):
    root = Path(__file__).resolve().parents[1]
    result = Config(str(root / "alembic.ini"), output_buffer=output)
    result.set_main_option("script_location", str(root / "migrations"))
    result.set_main_option("sqlalchemy.url", "postgresql://synthetic:synthetic@localhost/synthetic")
    return result


def test_wbs_is_single_sequential_head():
    scripts = ScriptDirectory.from_config(config())
    assert scripts.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a21"]
    assert scripts.get_revision(CURRENT_SCHEMA_REVISION).down_revision == "a54f001c0a20"


def test_wbs_upgrade_and_fail_closed_downgrade_sql():
    upgrade = StringIO(); command.upgrade(config(upgrade), "a54f001c0a20:a54f001c0a21", sql=True)
    sql = upgrade.getvalue()
    assert "ADD COLUMN wbs_parent_id INTEGER" in sql
    assert "ADD COLUMN wbs_order INTEGER DEFAULT '0' NOT NULL" in sql
    assert "ADD COLUMN is_summary BOOLEAN DEFAULT false NOT NULL" in sql
    assert "FOREIGN KEY(wbs_parent_id) REFERENCES schedule_items (id) ON DELETE RESTRICT" in sql
    assert "ck_schedule_summary_intent" in sql

    downgrade = StringIO(); command.downgrade(config(downgrade), "a54f001c0a21:a54f001c0a20", sql=True)
    down = downgrade.getvalue()
    assert "schedule_wbs_downgrade_requires_verified_restore" in down
    assert down.index("schedule_wbs_downgrade_requires_verified_restore") < down.index("DROP COLUMN wbs_parent_id")
