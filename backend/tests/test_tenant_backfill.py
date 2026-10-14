"""Unit tests for owner-workspace backfill helper (portable SQL).

The task explicitly permits testing the backfill function directly if running
the whole Alembic migration on SQLite is brittle. We use an in-memory SQLite
schema built from the ORM metadata, then exercise ``backfill_owner_workspace``
through ``connection.execute``-based SQL (portable between PostgreSQL and
SQLite).
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register all mapped tables
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.user import User
from app.tenancy import (
    OWNER_WORKSPACE_NAME,
    OWNER_WORKSPACE_SLUG,
    backfill_owner_workspace,
)


def _make_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_backfill_assigns_owner_workspace_and_membership():
    engine = _make_engine()
    with Session(engine) as db:
        user = User(name="Owner user", email="owner@example.test", is_admin=False)
        db.add(user); db.flush()
        org = Organization(name="Org")
        db.add(org); db.flush()
        project = Project(name="P", organization_id=org.id)
        db.add(project); db.flush()
        org_id, project_id, user_id = org.id, project.id, user.id
        db.commit()

    with engine.begin() as connection:
        backfill_owner_workspace(connection)

    with engine.connect() as connection:
        owner_id = connection.scalar(
            text("SELECT id FROM tenant_workspaces WHERE slug = :slug"),
            {"slug": OWNER_WORKSPACE_SLUG},
        )
        assert owner_id is not None
        ws_name = connection.scalar(
            text("SELECT name FROM tenant_workspaces WHERE id = :id"),
            {"id": owner_id},
        )
        assert ws_name == OWNER_WORKSPACE_NAME
        ws_plan = connection.scalar(
            text("SELECT plan FROM tenant_workspaces WHERE id = :id"),
            {"id": owner_id},
        )
        assert ws_plan == "start"
        assert connection.scalar(
            text("SELECT tenant_workspace_id FROM projects WHERE id = :id"),
            {"id": project_id},
        ) == owner_id
        assert connection.scalar(
            text("SELECT tenant_workspace_id FROM organizations WHERE id = :id"),
            {"id": org_id},
        ) == owner_id
        assert connection.scalar(
            text(
                "SELECT role FROM tenant_workspace_members "
                "WHERE user_id = :u AND workspace_id = :w"
            ),
            {"u": user_id, "w": owner_id},
        ) == "owner"

    engine.dispose()


def test_backfill_is_idempotent_on_repeat_run():
    engine = _make_engine()
    with Session(engine) as db:
        user = User(name="Owner user", email="owner@example.test", is_admin=False)
        db.add(user); db.flush()
        org = Organization(name="Org")
        db.add(org); db.flush()
        project = Project(name="P", organization_id=org.id)
        db.add(project); db.flush()
        db.commit()

    with engine.begin() as connection:
        backfill_owner_workspace(connection)
    with engine.begin() as connection:
        backfill_owner_workspace(connection)

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM tenant_workspaces WHERE slug = :slug"),
            {"slug": OWNER_WORKSPACE_SLUG},
        ) == 1
        assert connection.scalar(
            text("SELECT COUNT(*) FROM tenant_workspace_members")
        ) == 1
        assert connection.scalar(
            text(
                "SELECT COUNT(*) FROM projects WHERE tenant_workspace_id IS NULL"
            )
        ) == 0
        assert connection.scalar(
            text(
                "SELECT COUNT(*) FROM organizations WHERE tenant_workspace_id IS NULL"
            )
        ) == 0

    engine.dispose()
