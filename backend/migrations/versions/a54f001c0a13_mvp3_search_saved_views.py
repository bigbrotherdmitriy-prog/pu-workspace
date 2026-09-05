"""MVP3 project search saved views.

Revision ID: a54f001c0a13
Revises: a54f001c0a12
"""
from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a13"
down_revision = "a54f001c0a12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "saved_search_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(20), server_default="active", nullable=False),
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("record_version > 0", name="ck_saved_search_view_version"),
        sa.CheckConstraint("state IN ('active','deleted')", name="ck_saved_search_view_state"),
    )
    for column in ("organization_id", "project_id", "owner_user_id", "state"):
        op.create_index(f"ix_saved_search_views_{column}", "saved_search_views", [column])

    op.create_table(
        "saved_search_view_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("view_id", sa.Integer(), sa.ForeignKey("saved_search_views.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(30), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("view_id", "sequence", name="uq_saved_search_view_history_sequence"),
    )
    for column in ("view_id", "organization_id", "project_id", "owner_user_id"):
        op.create_index(f"ix_saved_search_view_history_{column}", "saved_search_view_history", [column])


def downgrade():
    op.drop_table("saved_search_view_history")
    op.drop_table("saved_search_views")
