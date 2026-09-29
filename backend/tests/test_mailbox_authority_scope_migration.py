import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_upgrade_preserves_grants_and_downgrade_revokes_scoped_grants():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/c70a0090f001_mailbox_authority_scope.py"
    spec = importlib.util.spec_from_file_location("mailbox_scope_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "c70a00a2f001"
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.execute(text("CREATE TABLE v54_mailbox_authority_states (id INTEGER PRIMARY KEY, state TEXT, authority_version INTEGER)"))
        db.execute(text("INSERT INTO v54_mailbox_authority_states VALUES (1,'active',7),(2,'active',3)"))
        migration.op = Operations(MigrationContext.configure(db))
        migration.upgrade()
        assert db.execute(text("SELECT scope_project_id,scope_credential_generation FROM v54_mailbox_authority_states WHERE id=1")).one() == (None, None)
        db.execute(text("UPDATE v54_mailbox_authority_states SET scope_project_id=17,scope_credential_generation=9 WHERE id=1"))
        migration.downgrade()
        assert db.execute(text("SELECT state,authority_version FROM v54_mailbox_authority_states WHERE id=1")).one() == ("revoked", 8)
        assert db.execute(text("SELECT state,authority_version FROM v54_mailbox_authority_states WHERE id=2")).one() == ("active", 3)
        assert "scope_project_id" not in {c["name"] for c in inspect(db).get_columns("v54_mailbox_authority_states")}
    engine.dispose()
