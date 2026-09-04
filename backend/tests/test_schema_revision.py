from io import StringIO
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


def test_expected_schema_revision_matches_single_alembic_head():
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f"Expected one Alembic head, got {heads}"
    assert CURRENT_SCHEMA_REVISION == heads[0]


def test_offline_migration_does_not_disable_application_loggers(monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    logger = logging.getLogger("pu.local_upload_staging")
    logger.disabled = False
    config = Config(str(backend / "alembic.ini"), output_buffer=StringIO())
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline",
    )

    command.upgrade(config, "a54f001c0a06:a54f001c0a07", sql=True)

    assert logger.disabled is False
