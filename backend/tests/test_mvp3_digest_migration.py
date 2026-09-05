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


def test_digest_schema_is_single_sequential_head():
    script = ScriptDirectory.from_config(_config())
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a17"]
    assert script.get_revision("a54f001c0a17").down_revision == "a54f001c0a16"


def test_digest_migration_contains_preferences_origins_and_guards():
    output = StringIO()
    command.upgrade(_config(output), "a54f001c0a16:a54f001c0a17", sql=True)
    sql = output.getvalue().lower()
    for token in (
        "management_digest_preferences",
        "management_proposal_origins",
        "uq_management_digest_preference_scope",
        "uq_management_proposal_origin_target",
        "ck_management_digest_preference_version",
        "ck_management_digest_preference_channel",
        "ck_management_digest_preference_cadence",
        "ck_management_proposal_origin_type",
        "ck_management_proposal_entity_type",
        "ck_management_proposal_kind",
    ):
        assert token in sql
