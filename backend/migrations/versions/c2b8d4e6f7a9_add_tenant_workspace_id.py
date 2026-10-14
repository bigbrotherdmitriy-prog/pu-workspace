"""Add tenant_workspace_id to projects and organizations with owner backfill.

Revision contains data migration only via op.execute() with portable SQL,
so it works both in online mode and in Alembic offline (--sql) mode.
"""

from alembic import op
import sqlalchemy as sa


revision = "c2b8d4e6f7a9"
down_revision = "b1a7c3d2e4f5"
branch_labels = None
depends_on = None


OWNER_NAME = "Пространство владельца"
OWNER_SLUG = "owner"
OWNER_PLAN = "start"


def upgrade():
    op.add_column(
        "projects",
        sa.Column("tenant_workspace_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("tenant_workspace_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_projects_tenant_workspace_id",
        "projects",
        ["tenant_workspace_id"],
    )
    op.create_index(
        "ix_organizations_tenant_workspace_id",
        "organizations",
        ["tenant_workspace_id"],
    )

    op.execute(
        sa.text(
            "INSERT INTO tenant_workspaces (name, slug, plan) "
            "SELECT :name, :slug, :plan "
            "WHERE NOT EXISTS (SELECT 1 FROM tenant_workspaces WHERE slug = :slug)"
        ).bindparams(name=OWNER_NAME, slug=OWNER_SLUG, plan=OWNER_PLAN)
    )
    op.execute(
        sa.text(
            "INSERT INTO tenant_workspace_members (user_id, workspace_id, role) "
            "SELECT u.id, w.id, 'owner' "
            "FROM users u CROSS JOIN tenant_workspaces w "
            "WHERE w.slug = :slug "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM tenant_workspace_members m "
            "  WHERE m.user_id = u.id AND m.workspace_id = w.id"
            ")"
        ).bindparams(slug=OWNER_SLUG)
    )
    op.execute(
        sa.text(
            "UPDATE projects SET tenant_workspace_id = "
            "(SELECT id FROM tenant_workspaces WHERE slug = :slug) "
            "WHERE tenant_workspace_id IS NULL"
        ).bindparams(slug=OWNER_SLUG)
    )
    op.execute(
        sa.text(
            "UPDATE organizations SET tenant_workspace_id = "
            "(SELECT id FROM tenant_workspaces WHERE slug = :slug) "
            "WHERE tenant_workspace_id IS NULL"
        ).bindparams(slug=OWNER_SLUG)
    )


def downgrade():
    op.drop_index("ix_organizations_tenant_workspace_id", table_name="organizations")
    op.drop_index("ix_projects_tenant_workspace_id", table_name="projects")
    op.drop_column("organizations", "tenant_workspace_id")
    op.drop_column("projects", "tenant_workspace_id")
