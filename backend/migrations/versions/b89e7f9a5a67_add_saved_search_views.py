"""Add private versioned saved search views.

Revision ID: b89e7f9a5a67
Revises: b88e7f9a4f56
"""

from alembic import op
import sqlalchemy as sa


revision = "b89e7f9a5a67"
down_revision = "b88e7f9a4f56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_search_views",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("record_version > 0", name="ck_saved_search_views_record_version"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            name="fk_saved_search_views_project_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_saved_search_views_organization_id", "saved_search_views", ["organization_id"])
    op.create_index("ix_saved_search_views_project_id", "saved_search_views", ["project_id"])
    op.create_index("ix_saved_search_views_owner_user_id", "saved_search_views", ["owner_user_id"])
    op.create_index("ix_saved_search_views_deleted_at", "saved_search_views", ["deleted_at"])
    op.create_index(
        "uq_saved_search_views_active_owner_name",
        "saved_search_views",
        ["project_id", "owner_user_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_saved_search_views_active_owner_name", table_name="saved_search_views")
    op.drop_index("ix_saved_search_views_deleted_at", table_name="saved_search_views")
    op.drop_index("ix_saved_search_views_owner_user_id", table_name="saved_search_views")
    op.drop_index("ix_saved_search_views_project_id", table_name="saved_search_views")
    op.drop_index("ix_saved_search_views_organization_id", table_name="saved_search_views")
    op.drop_table("saved_search_views")
