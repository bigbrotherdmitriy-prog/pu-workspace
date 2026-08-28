"""add message attachment metadata

Revision ID: a61b2c3d4e5f
Revises: e52a1d8c9f20
"""
from alembic import op
import sqlalchemy as sa

revision = "a61b2c3d4e5f"
down_revision = "e52a1d8c9f20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("attachments_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("messages", "attachments_json")
