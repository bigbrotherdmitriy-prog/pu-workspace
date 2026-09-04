"""add ai secretary inbox and proposed external actions

Revision ID: b71d92e4c105
Revises: a17c4d820e31
"""
from alembic import op
import sqlalchemy as sa

revision = "b71d92e4c105"
down_revision = "a17c4d820e31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_external_id", sa.String(500), nullable=False),
        sa.Column("source_name", sa.String(1000), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("context_confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("context_evidence", sa.Text(), nullable=False),
        sa.Column("context_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(50), nullable=False, server_default="needs_review"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_type", "source_external_id", name="uq_message_source"),
    )
    for column in ("organization_id", "project_id", "contract_id", "created_by_user_id", "source_type", "status"):
        op.create_index(f"ix_messages_{column}", "messages", [column])
    op.add_column("tasks", sa.Column("message_id", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("external_action_status", sa.String(30), nullable=False, server_default="proposed"))
    op.create_foreign_key("fk_tasks_message", "tasks", "messages", ["message_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_tasks_message_id", "tasks", ["message_id"])
    op.create_index("ix_tasks_external_action_status", "tasks", ["external_action_status"])
    op.execute("UPDATE tasks SET external_action_status='executed' WHERE google_task_id IS NOT NULL OR google_calendar_event_id IS NOT NULL")
    op.add_column("response_drafts", sa.Column("message_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_response_drafts_message", "response_drafts", "messages", ["message_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_response_drafts_message_id", "response_drafts", ["message_id"])


def downgrade():
    op.drop_index("ix_response_drafts_message_id", table_name="response_drafts")
    op.drop_constraint("fk_response_drafts_message", "response_drafts", type_="foreignkey")
    op.drop_column("response_drafts", "message_id")
    op.drop_index("ix_tasks_external_action_status", table_name="tasks")
    op.drop_index("ix_tasks_message_id", table_name="tasks")
    op.drop_constraint("fk_tasks_message", "tasks", type_="foreignkey")
    op.drop_column("tasks", "external_action_status")
    op.drop_column("tasks", "message_id")
    op.drop_table("messages")
