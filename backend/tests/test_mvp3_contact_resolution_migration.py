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


def test_contact_resolution_is_one_sequential_local_head():
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a15"]
    assert script.get_revision("a54f001c0a12").down_revision == "a54f001c0a11"


def test_contact_resolution_offline_upgrade_contains_scope_cas_and_history(monkeypatch):
    output = StringIO()
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_contact_offline",
    )
    command.upgrade(config(output), "a54f001c0a11:a54f001c0a12", sql=True)
    sql = output.getvalue()
    for token in (
        "mail_connection_id", "normalized_domain", "normalized_phone", "record_version",
        "resolution_state", "project_contact_history", "decision_key", "command_hash",
        "uq_project_contact_mailbox_project_email",
    ):
        assert token in sql
