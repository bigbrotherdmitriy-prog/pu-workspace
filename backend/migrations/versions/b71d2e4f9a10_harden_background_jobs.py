"""harden background jobs

Revision ID: b71d2e4f9a10
Revises: a31c7d8e9f20
"""
from alembic import op
import sqlalchemy as sa

revision = "b71d2e4f9a10"
down_revision = "a31c7d8e9f20"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("background_jobs", sa.Column("progress", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("background_jobs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("background_jobs", sa.Column("duration_ms", sa.Integer()))
    op.add_column("background_jobs", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE background_jobs SET status='completed' WHERE status='succeeded'")

def downgrade():
    op.execute("UPDATE background_jobs SET status='succeeded' WHERE status='completed'")
    op.drop_column("background_jobs", "cancelled_at")
    op.drop_column("background_jobs", "duration_ms")
    op.drop_column("background_jobs", "started_at")
    op.drop_column("background_jobs", "progress")
