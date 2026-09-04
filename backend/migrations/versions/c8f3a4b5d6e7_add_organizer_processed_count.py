"""add exact organizer processed item counter

Revision ID: c8f3a4b5d6e7
Revises: b7e1a2c3d4f5
"""

from alembic import op
import sqlalchemy as sa


revision = "c8f3a4b5d6e7"
down_revision = "b7e1a2c3d4f5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organizer_sessions",
        sa.Column("processed_item_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("organizer_sessions", "processed_item_count")
