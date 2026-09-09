from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


def _config(output=None):
    backend = Path(__file__).resolve().parents[1]
    result = Config(str(backend / "alembic.ini"), output_buffer=output)
    result.set_main_option("script_location", str(backend / "migrations"))
    result.set_main_option("sqlalchemy.url", "postgresql://synthetic:synthetic@localhost/synthetic")
    return result


def test_storage_oauth_state_is_the_single_sequential_head():
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a22"]
    assert scripts.get_revision(CURRENT_SCHEMA_REVISION).down_revision == "a54f001c0a21"


def test_storage_oauth_state_offline_sql_has_scoped_single_use_state():
    output = StringIO()
    command.upgrade(_config(output), "a54f001c0a21:a54f001c0a22", sql=True)
    sql = output.getvalue().lower()
    for token in (
        "create table storage_oauth_states",
        "state_hash varchar(64) not null",
        "organization_id integer not null",
        "project_id integer not null",
        "user_id integer not null",
        "expires_at timestamp with time zone not null",
        "consumed_at timestamp with time zone",
        "fk_storage_oauth_state_project_scope",
        "foreign key(organization_id, project_id)",
        "primary key (state_hash)",
    ):
        assert token in sql
