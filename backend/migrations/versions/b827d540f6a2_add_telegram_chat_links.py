"""add telegram chat links

Revision ID: b827d540f6a2
Revises: a4e82ef91640
"""
from alembic import op
import sqlalchemy as sa

revision = "b827d540f6a2"
down_revision = "a4e82ef91640"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_chat_links",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telegram_chat_links_project_id", "telegram_chat_links", ["project_id"])
    op.create_index("ix_telegram_chat_links_enabled", "telegram_chat_links", ["enabled"])


def downgrade():
    op.drop_table("telegram_chat_links")
