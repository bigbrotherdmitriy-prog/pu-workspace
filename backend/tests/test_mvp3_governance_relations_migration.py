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


def test_governance_relations_are_the_single_sequential_head():
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [CURRENT_SCHEMA_REVISION] == ["b17c4d2e6f90"]
    assert scripts.get_revision("b89e7f9a5a67").down_revision == "b88e7f9a4f56"
    assert set(scripts.get_revision("b88e7f9a4f56").down_revision) == {
        "b86e7f9a1d24", "b87e7f9a3f45",
    }
    assert scripts.get_revision("b87e7f9a3f45").down_revision == "b86e7f9a2e34"


def test_governance_relations_render_postgresql_foreign_keys(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_mvp3_test",
    )
    output = StringIO()
    command.upgrade(_config(output), "b86e7f9a2e34:b87e7f9a3f45", sql=True)
    sql = output.getvalue()
    for token in (
        "fk_risks_obligation", "fk_risks_task", "fk_decisions_obligation",
        "fk_decisions_task", "fk_decisions_risk",
    ):
        assert token in sql
    assert "DROP TABLE" not in sql
