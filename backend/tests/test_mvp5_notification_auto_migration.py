from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


ROOT = Path(__file__).resolve().parents[1]


def test_notification_auto_migration_is_the_single_head():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == [CURRENT_SCHEMA_REVISION]
    assert CURRENT_SCHEMA_REVISION == "c70a00a1f001"


def test_notification_auto_migration_only_extends_the_closed_action_catalog():
    source = (ROOT / "migrations" / "versions" /
              "a54f001c0a10_add_internal_notification_auto.py").read_text(encoding="utf-8")
    assert 'down_revision = "e26b8d0f3c51"' in source
    assert "notification.internal.create" in source
    assert "drop_constraint(\"ck_v54_action_type\"" in source
    assert "Internal notification actions must be archived before downgrade" in source
