"""Owner workspace bootstrap/backfill helpers.

Portable (PostgreSQL + SQLite) SQL used by the tenant bootstrap migration.
All statements are idempotent and safe to re-run: no rows are duplicated.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

OWNER_WORKSPACE_NAME = "Пространство владельца"
OWNER_WORKSPACE_SLUG = "owner"
OWNER_WORKSPACE_PLAN = "start"


def backfill_owner_workspace(connection: Connection) -> None:
    """Create owner workspace, memberships and backfill tenant_workspace_id."""
    connection.execute(
        text(
            "INSERT INTO tenant_workspaces (name, slug, plan) "
            "SELECT :name, :slug, :plan "
            "WHERE NOT EXISTS (SELECT 1 FROM tenant_workspaces WHERE slug = :slug)"
        ),
        {"name": OWNER_WORKSPACE_NAME, "slug": OWNER_WORKSPACE_SLUG, "plan": OWNER_WORKSPACE_PLAN},
    )
    connection.execute(
        text(
            "INSERT INTO tenant_workspace_members (user_id, workspace_id, role) "
            "SELECT u.id, w.id, 'owner' "
            "FROM users u CROSS JOIN tenant_workspaces w "
            "WHERE w.slug = :slug "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM tenant_workspace_members m "
            "  WHERE m.user_id = u.id AND m.workspace_id = w.id"
            ")"
        ),
        {"slug": OWNER_WORKSPACE_SLUG},
    )
    connection.execute(
        text(
            "UPDATE projects SET tenant_workspace_id = "
            "(SELECT id FROM tenant_workspaces WHERE slug = :slug) "
            "WHERE tenant_workspace_id IS NULL"
        ),
        {"slug": OWNER_WORKSPACE_SLUG},
    )
    connection.execute(
        text(
            "UPDATE organizations SET tenant_workspace_id = "
            "(SELECT id FROM tenant_workspaces WHERE slug = :slug) "
            "WHERE tenant_workspace_id IS NULL"
        ),
        {"slug": OWNER_WORKSPACE_SLUG},
    )
