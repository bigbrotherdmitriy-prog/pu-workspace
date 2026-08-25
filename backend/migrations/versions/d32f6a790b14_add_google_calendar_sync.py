"""add google calendar sync

Revision ID: d32f6a790b14
Revises: c91576ea302d
"""
from alembic import op
import sqlalchemy as sa

revision = "d32f6a790b14"
down_revision = "c91576ea302d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("google_calendar_event_id", sa.String(255), nullable=True))
    op.add_column("tasks", sa.Column("google_calendar_sync_error", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("google_calendar_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tasks_google_calendar_event_id", "tasks", ["google_calendar_event_id"])


def downgrade():
    op.drop_index("ix_tasks_google_calendar_event_id", table_name="tasks")
    op.drop_column("tasks", "google_calendar_synced_at")
    op.drop_column("tasks", "google_calendar_sync_error")
    op.drop_column("tasks", "google_calendar_event_id")
