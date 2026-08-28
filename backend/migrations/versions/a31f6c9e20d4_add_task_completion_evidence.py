"""add optional task completion evidence and lifecycle history

Revision ID: a31f6c9e20d4
Revises: d8b2f14c7a30
"""
from alembic import op
import sqlalchemy as sa

revision = "a31f6c9e20d4"
down_revision = "d8b2f14c7a30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("completion_document_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_tasks_completion_document", "tasks", "documents", ["completion_document_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_tasks_completion_document_id", "tasks", ["completion_document_id"])
    op.create_table(
        "task_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("old_status", sa.String(length=50), nullable=True),
        sa.Column("new_status", sa.String(length=50), nullable=True),
        sa.Column("result_note", sa.Text(), nullable=True),
        sa.Column("completion_document_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["completion_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_task_history_task_id", "task_history", ["task_id"])


def downgrade():
    op.drop_index("ix_task_history_task_id", table_name="task_history")
    op.drop_table("task_history")
    op.drop_index("ix_tasks_completion_document_id", table_name="tasks")
    op.drop_constraint("fk_tasks_completion_document", "tasks", type_="foreignkey")
    op.drop_column("tasks", "completion_document_id")
