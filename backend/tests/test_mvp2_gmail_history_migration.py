"""Schema proofs for the sequential Gmail history cursor revision."""

from io import StringIO
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]
TABLES = {
    "v54_gmail_history_checkpoints",
    "v54_gmail_history_checkpoint_events",
}


def config(output=None):
    value = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    value.set_main_option("script_location", str(BACKEND / "migrations"))
    return value


def test_history_revision_is_single_sequential_head_and_offline_safe(monkeypatch):
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a18"]
    assert script.get_revision("a54f001c0a18").down_revision == "a54f001c0a17"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_mvp2_test_offline",
    )
    output = StringIO()
    command.upgrade(config(output), "a54f001c0a17:a54f001c0a18", sql=True)
    sql = output.getvalue()
    for token in (
        "v54_gmail_history_checkpoints",
        "v54_gmail_history_checkpoint_events",
        "uq_v54_gmail_history_checkpoint_mailbox",
        "fk_v54_gmail_history_checkpoint_identity",
        "fk_v54_gmail_history_checkpoint_mail",
        "fk_v54_gmail_history_event_checkpoint",
        "ck_v54_gmail_history_checkpoint_claim",
        "ck_v54_gmail_history_event_outcome",
    ):
        assert token in sql
    assert "DROP TABLE" not in sql
    assert "provider_message_id" not in sql
    assert "email" not in sql.casefold()


def _postgres_url():
    value = os.getenv("PUW_MVP2_GMAIL_HISTORY_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: Gmail history PostgreSQL URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_mvp2_test_") and not parsed.query
    return value


def test_postgresql_history_schema_and_cas_constraints():
    url = _postgres_url()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
            inspector = inspect(connection)
            assert TABLES <= set(inspector.get_table_names())
            assert {
                "uq_v54_gmail_history_checkpoint_mailbox",
                "uq_v54_gmail_history_checkpoint_scope",
            } <= {
                value["name"] for value in inspector.get_unique_constraints(
                    "v54_gmail_history_checkpoints"
                )
            }
            assert connection.scalar(text(
                "SELECT count(*) FROM v54_gmail_history_checkpoint_events"
            )) >= 0
    finally:
        engine.dispose()
