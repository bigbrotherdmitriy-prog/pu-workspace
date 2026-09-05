from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


def test_meeting_binding_migration_is_sequential_and_preserves_unbound_legacy():
    backend = Path(__file__).resolve().parents[1]
    output = StringIO()
    config = Config(str(backend / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(backend / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a19"]
    assert script.get_revision(CURRENT_SCHEMA_REVISION).down_revision == "a54f001c0a18"
    command.upgrade(config, "a54f001c0a18:a54f001c0a19", sql=True)
    sql = output.getvalue().lower()
    for token in ("meeting_source_bindings", "fk_meeting_binding_observation", "fk_meeting_binding_meeting",
                  "fk_proposal_meeting_binding", "meeting_binding_immutable", "before update or delete",
                  "uq_meeting_binding_command", "uq_meeting_binding_version", "record_version integer default '1'"):
        assert token in sql
    assert "update management_proposal_origins" not in sql
