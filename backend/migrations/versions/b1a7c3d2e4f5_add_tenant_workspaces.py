"""Add tenant_workspaces and tenant_workspace_members."""

from alembic import op
import sqlalchemy as sa


revision = "b1a7c3d2e4f5"
down_revision = "1deff6634d0d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tenant_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("plan", sa.String(50), nullable=False, server_default="start"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_tenant_workspaces_slug"),
    )

    op.create_table(
        "tenant_workspace_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("tenant_workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_tenant_workspace_member"),
    )
    op.create_index("ix_tenant_workspace_members_user_id", "tenant_workspace_members", ["user_id"])
    op.create_index("ix_tenant_workspace_members_workspace_id", "tenant_workspace_members", ["workspace_id"])


def downgrade():
    op.drop_index("ix_tenant_workspace_members_workspace_id", table_name="tenant_workspace_members")
    op.drop_index("ix_tenant_workspace_members_user_id", table_name="tenant_workspace_members")
    op.drop_table("tenant_workspace_members")
    op.drop_table("tenant_workspaces")
