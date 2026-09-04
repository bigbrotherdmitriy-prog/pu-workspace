"""add outgoing task completion suggestions

Revision ID: f47a50d44d91
Revises: e6b24a91c301
"""
from alembic import op
import sqlalchemy as sa

revision = "f47a50d44d91"
down_revision = "e6b24a91c301"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_completion_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="proposed"),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("message_id", "task_id", name="uq_completion_suggestion_message_task"),
    )
    for column in ("project_id", "message_id", "task_id", "status"):
        op.create_index(f"ix_task_completion_suggestions_{column}", "task_completion_suggestions", [column])


def downgrade():
    op.drop_table("task_completion_suggestions")
