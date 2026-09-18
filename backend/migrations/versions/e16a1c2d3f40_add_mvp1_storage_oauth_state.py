"""Add single-use OAuth state for the MVP-1 storage port.

Revision ID: e16a1c2d3f40
Revises: d04e8a6c31f2
"""

from alembic import op
import sqlalchemy as sa


revision = "e16a1c2d3f40"
down_revision = "d04e8a6c31f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "storage_oauth_states",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            name="fk_storage_oauth_state_project_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index(
        "ix_storage_oauth_states_organization_id",
        "storage_oauth_states",
        ["organization_id"],
    )
    op.create_index(
        "ix_storage_oauth_states_project_id",
        "storage_oauth_states",
        ["project_id"],
    )
    op.create_index(
        "ix_storage_oauth_states_user_id",
        "storage_oauth_states",
        ["user_id"],
    )
    op.create_index(
        "ix_storage_oauth_states_expires_at",
        "storage_oauth_states",
        ["expires_at"],
    )


def downgrade():
    op.drop_table("storage_oauth_states")
