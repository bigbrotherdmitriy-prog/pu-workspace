"""add task lifecycle

Revision ID: e47b9c128a55
Revises: d32f6a790b14
"""
from alembic import op
import sqlalchemy as sa

revision = "e47b9c128a55"
down_revision = "d32f6a790b14"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("result_note", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "task_due_date_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_due_date", sa.Date(), nullable=True),
        sa.Column("new_due_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_task_due_date_history_task_id", "task_due_date_history", ["task_id"])


def downgrade():
    op.drop_table("task_due_date_history")
    op.drop_column("tasks", "completed_at")
    op.drop_column("tasks", "result_note")
