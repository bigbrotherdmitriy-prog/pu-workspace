"""Schema proofs for the sequential synthetic provider action revision."""
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
PROVIDER_TABLES = [
    "v54_provider_actions",
    "v54_provider_action_approvals",
    "v54_provider_dispatch_outbox",
    "v54_provider_execution_attempts",
    "v54_provider_outcome_observations",
]


def migration_config(output_buffer=None):
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output_buffer)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_provider_revision_is_the_only_head_and_renders_postgresql_offline(monkeypatch):
    assert ScriptDirectory.from_config(migration_config()).get_heads() == [CURRENT_SCHEMA_REVISION]
    assert CURRENT_SCHEMA_REVISION == "a54f001c0a16"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline",
    )
    output = StringIO()
    command.upgrade(migration_config(output), "a54f001c0a05:a54f001c0a06", sql=True)
    sql = output.getvalue()

    offsets = [sql.index(f"CREATE TABLE {table}") for table in PROVIDER_TABLES]
    assert offsets == sorted(offsets)
    for constraint in (
        "uq_v54_provider_action_scope",
        "uq_v54_provider_command",
        "uq_v54_provider_idempotency",
        "fk_v54_provider_approval_action",
        "uq_v54_provider_approval_binding",
        "fk_v54_provider_outbox_action",
        "fk_v54_provider_outbox_approval",
        "fk_v54_provider_attempt_action",
        "fk_v54_provider_observation_action",
        "uq_v54_provider_observation_sequence",
        "ix_v54_provider_outbox_pending",
    ):
        assert constraint in sql
    assert "mode = 'CONFIRM'" in sql
    assert "provider = 'synthetic'" in sql
    assert "synthetic_only = true" in sql
    assert "raw_payload" not in sql and "payload_json" not in sql
    assert "DROP TABLE" not in sql and "INSERT INTO v54_provider" not in sql


def test_product_outbox_policy_is_sequential_and_fail_closed(monkeypatch):
    script = ScriptDirectory.from_config(migration_config())
    assert script.get_revision("a54f001c0a15").down_revision == "a54f001c0a14"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline",
    )
    output = StringIO()
    command.upgrade(migration_config(output), "a54f001c0a14:a54f001c0a15", sql=True)
    sql = output.getvalue()

    assert "DROP CONSTRAINT ck_v54_provider_confirm_synthetic" in sql
    assert "gmail.message.send" in sql
    assert "google.tasks.upsert" in sql
    assert "google.calendar.upsert" in sql
    assert "provider = 'google_workspace'" in sql
    assert "mode = 'CONFIRM'" in sql
    assert "synthetic_only = false" in sql
    assert "AUTO" not in sql


def safe_postgres_url():
    value = os.getenv("PUW_V54_PROVIDER_MIGRATION_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: provider migration PostgreSQL URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_v54_test_") and not parsed.query
    return value


def test_postgresql_provider_upgrade_from_a05_and_round_trip(monkeypatch):
    url = safe_postgres_url()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    monkeypatch.setenv("DATABASE_URL", url)
    config = migration_config()
    try:
        inspector = inspect(engine)
        assert set(PROVIDER_TABLES) <= set(inspector.get_table_names())
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
            for table in PROVIDER_TABLES:
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0

        action_fks = {
            fk["name"]
            for table in PROVIDER_TABLES[1:]
            for fk in inspector.get_foreign_keys(table)
        }
        assert {
            "fk_v54_provider_approval_action",
            "fk_v54_provider_outbox_action",
            "fk_v54_provider_attempt_action",
            "fk_v54_provider_observation_action",
            "fk_v54_provider_outbox_approval",
        } <= action_fks
        assert "ix_v54_provider_outbox_pending" in {
            index["name"] for index in inspector.get_indexes("v54_provider_dispatch_outbox")
        }

        command.downgrade(config, "a54f001c0a05")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "a54f001c0a05"
            assert not set(PROVIDER_TABLES) & set(inspect(connection).get_table_names())
        command.upgrade(config, CURRENT_SCHEMA_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
            assert set(PROVIDER_TABLES) <= set(inspect(connection).get_table_names())
    finally:
        engine.dispose()
