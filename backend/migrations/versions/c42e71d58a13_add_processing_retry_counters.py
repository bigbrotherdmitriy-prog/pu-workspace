"""add processing retry counters

Revision ID: c42e71d58a13
Revises: b31d8a7f4c02
"""
from alembic import op
import sqlalchemy as sa

revision = "c42e71d58a13"
down_revision = "b31d8a7f4c02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizer_sessions", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("workspace_snapshots", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("workspace_snapshots", sa.Column("analysis_retry_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("workspace_snapshots", "analysis_retry_count")
    op.drop_column("workspace_snapshots", "retry_count")
    op.drop_column("organizer_sessions", "retry_count")
