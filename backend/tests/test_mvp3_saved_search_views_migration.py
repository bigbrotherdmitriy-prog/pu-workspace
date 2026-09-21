from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION

BACKEND = Path(__file__).resolve().parents[1]


def _config(output=None):
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_saved_search_views_migration_is_the_single_head():
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a10"]
    revision = scripts.get_revision("b89e7f9a5a67")
    assert revision.down_revision == "b88e7f9a4f56"


def test_saved_search_views_migration_has_scoped_versioned_private_contract():
    migration = (BACKEND / "migrations" / "versions" / "b89e7f9a5a67_add_saved_search_views.py").read_text(
        encoding="utf-8",
    )
    for token in (
        '"saved_search_views"', '"record_version"', '"organization_id"', '"project_id"',
        '"owner_user_id"', '"filters"', '"deleted_at"',
        '"fk_saved_search_views_project_scope"', '"uq_saved_search_views_active_owner_name"',
    ):
        assert token in migration
