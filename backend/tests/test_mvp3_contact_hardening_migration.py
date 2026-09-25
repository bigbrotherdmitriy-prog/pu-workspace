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


def test_contact_hardening_is_merged_into_the_single_current_head():
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [CURRENT_SCHEMA_REVISION] == ["c70a00a1f001"]
    assert scripts.get_revision("b09c4f1d2e73").down_revision == "a72d4e6f8b91"
    assert set(scripts.get_revision("b86e7f9a2e34").down_revision) == {
        "b85e7f9a1d23", "b09c4f1d2e73",
    }


def test_contact_hardening_offline_sql_contains_scope_and_idempotency(monkeypatch):
    output = StringIO()
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_contact_test",
    )
    command.upgrade(_config(output), "a72d4e6f8b91:b09c4f1d2e73", sql=True)
    sql = output.getvalue()
    for token in (
        "mail_connection_id", "normalized_domain", "normalized_phone",
        "resolution_state", "uq_project_contact_mailbox_email",
        "idempotency_key", "command_hash", "uq_management_history_idempotency",
    ):
        assert token in sql
