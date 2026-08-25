"""add google task sync

Revision ID: c91576ea302d
Revises: b827d540f6a2
"""
from alembic import op
import sqlalchemy as sa

revision = "c91576ea302d"
down_revision = "b827d540f6a2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("google_task_id", sa.String(255), nullable=True))
    op.add_column("tasks", sa.Column("google_task_list_id", sa.String(255), nullable=True))
    op.add_column("tasks", sa.Column("google_sync_error", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("google_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tasks_google_task_id", "tasks", ["google_task_id"])


def downgrade():
    op.drop_index("ix_tasks_google_task_id", table_name="tasks")
    op.drop_column("tasks", "google_synced_at")
    op.drop_column("tasks", "google_sync_error")
    op.drop_column("tasks", "google_task_list_id")
    op.drop_column("tasks", "google_task_id")
