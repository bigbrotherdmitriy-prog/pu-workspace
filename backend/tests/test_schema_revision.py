from pathlib import Path

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
