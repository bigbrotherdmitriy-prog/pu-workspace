from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config


def _offline_upgrade_sql() -> str:
    backend = Path(__file__).resolve().parents[1]
    buffer = StringIO()
    config = Config(str(backend / "alembic.ini"), output_buffer=buffer)
    config.set_main_option("script_location", str(backend / "migrations"))
    command.upgrade(config, "c13606d92787:d29a6c4f1e83", sql=True)
    return buffer.getvalue()


def test_migration_adds_expected_columns_to_both_tables():
    sql = _offline_upgrade_sql()
    for table in ("tasks", "obligations"):
        for column in (
            "amount", "amount_currency", "amount_evidence_quote",
            "due_date_evidence_quote", "assignee_hint", "assignee_evidence_quote",
            "extraction_method",
        ):
            assert f"ADD COLUMN {column}" in sql, f"{table}.{column} not found in emitted SQL"


def test_migration_defaults_extraction_method_to_regex_for_existing_rows():
    sql = _offline_upgrade_sql()
    assert "extraction_method VARCHAR(20) DEFAULT 'regex' NOT NULL" in sql
