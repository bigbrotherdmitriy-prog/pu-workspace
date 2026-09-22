from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]
REVISION = "b83e7f9a1c02"
MERGE_REVISION = "b85e7f9a1d23"


def _config(output=None):
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_meeting_authority_is_merged_into_the_single_current_head():
    script = ScriptDirectory.from_config(_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == "a72d4e6f8b91"
    merge = script.get_revision(MERGE_REVISION)
    assert set(merge.down_revision) == {REVISION, "b84e6f9a7c12"}
    assert script.get_heads() == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a11"]


def test_meeting_authority_migration_renders_postgresql_constraints(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_mvp3_test_offline",
    )
    output = StringIO()
    command.upgrade(_config(output), f"a72d4e6f8b91:{REVISION}", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE meeting_source_bindings" in sql
    assert "CREATE TABLE meeting_proposals" in sql
    assert "fk_meeting_source_binding_observation" in sql
    assert "fk_meeting_source_binding_evidence" in sql
    assert "uq_meeting_source_binding_command" in sql
    assert "uq_meeting_proposal_fingerprint" in sql
    assert "DROP TABLE" not in sql
