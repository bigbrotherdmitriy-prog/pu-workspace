from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]


def _config(output=None):
    value = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    value.set_main_option("script_location", str(BACKEND / "migrations"))
    return value


def test_search_migration_is_the_only_temporary_head():
    script = ScriptDirectory.from_config(_config())
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a16"]
    assert script.get_revision("a54f001c0a13").down_revision == "a54f001c0a12"


def test_search_migration_offline_sql_has_scoped_views_cas_and_history(monkeypatch):
    output = StringIO()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_search_offline")
    command.upgrade(_config(output), "a54f001c0a12:a54f001c0a13", sql=True)
    sql = output.getvalue()
    for token in (
        "saved_search_views",
        "saved_search_view_history",
        "organization_id",
        "project_id",
        "owner_user_id",
        "record_version",
        "filters",
    ):
        assert token in sql
