from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND = Path(__file__).resolve().parents[1]
REVISION = "e16a1c2d3f40"


def test_storage_oauth_state_is_a_single_linear_migration():
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    script = ScriptDirectory.from_config(config)

    revision = script.get_revision(REVISION)

    assert revision is not None
    assert revision.down_revision == "d04e8a6c31f2"
    assert script.get_heads() == [REVISION]


def test_storage_oauth_state_migration_has_scoped_single_use_state():
    source = (BACKEND / "migrations" / "versions" /
              f"{REVISION}_add_mvp1_storage_oauth_state.py").read_text(encoding="utf-8")

    assert '"storage_oauth_states"' in source
    assert 'sa.PrimaryKeyConstraint("state_hash")' in source
    assert '"organization_id", "project_id"' in source
    assert '"projects.organization_id", "projects.id"' in source
    assert 'ondelete="CASCADE"' in source
    assert 'sa.Column("consumed_at"' in source
    assert 'sa.Column("expires_at"' in source
