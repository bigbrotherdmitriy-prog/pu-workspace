"""add project AI policy

Revision ID: b31d8a7f4c02
Revises: a12e94c71b20
"""
from alembic import op
import sqlalchemy as sa

revision = "b31d8a7f4c02"
down_revision = "a12e94c71b20"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("project_ai_policies",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("mode", sa.String(30), nullable=False, server_default="external_allowed"),
        sa.Column("dlp_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("prompt_version", sa.String(50), nullable=False, server_default="v1"),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))


def downgrade():
    op.drop_table("project_ai_policies")
