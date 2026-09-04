"""add automatic tasks

Revision ID: 9c31b5b53a21
Revises: 7b0f3e3d9a12
"""
from alembic import op
import sqlalchemy as sa

revision = "9c31b5b53a21"
down_revision = "7b0f3e3d9a12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignee_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organizer_session_id", sa.Integer(), sa.ForeignKey("organizer_sessions.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(50), nullable=False, server_default="assigned"),
        sa.Column("priority", sa.String(30), nullable=False, server_default="normal"),
        sa.Column("due_date", sa.Date()),
        sa.Column("source_type", sa.String(50), nullable=False, server_default="document_analysis"),
        sa.Column("source_file_id", sa.String(255), nullable=False),
        sa.Column("source_file_name", sa.String(1000), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("source_excerpt_hash", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "source_file_id", "source_excerpt_hash", name="uq_task_source_excerpt"),
    )
    for name, cols in {
        "ix_tasks_project_id": ["project_id"], "ix_tasks_assignee_user_id": ["assignee_user_id"],
        "ix_tasks_organizer_session_id": ["organizer_session_id"], "ix_tasks_status": ["status"],
        "ix_tasks_due_date": ["due_date"], "ix_tasks_source_file_id": ["source_file_id"],
    }.items():
        op.create_index(name, "tasks", cols)


def downgrade():
    op.drop_table("tasks")
