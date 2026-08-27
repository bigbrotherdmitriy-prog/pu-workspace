"""add MVP3 management registers

Revision ID: d64c27a113f0
Revises: b71d92e4c105
"""
from alembic import op
import sqlalchemy as sa

revision = "d64c27a113f0"
down_revision = "b71d92e4c105"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("obligations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False), sa.Column("status", sa.String(40), nullable=False, server_default="needs_confirmation"),
        sa.Column("due_date", sa.Date(), nullable=True), sa.Column("result_note", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False), sa.Column("source_id", sa.String(500), nullable=False),
        sa.Column("source_name", sa.String(1000), nullable=False), sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False), sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "source_id", "source_hash", name="uq_obligation_source"))
    for column in ("project_id", "contract_id", "owner_user_id", "task_id", "status", "due_date"):
        op.create_index(f"ix_obligations_{column}", "obligations", [column])
    op.create_table("meetings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False), sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("participants", sa.Text(), nullable=True), sa.Column("agenda", sa.Text(), nullable=True),
        sa.Column("minutes", sa.Text(), nullable=True), sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    for column in ("project_id", "contract_id", "scheduled_at", "status"):
        op.create_index(f"ix_meetings_{column}", "meetings", [column])
    op.create_table("notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False), sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False), sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False), sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notification_user_key"))
    for column in ("project_id", "user_id", "kind", "entity_id", "is_read"):
        op.create_index(f"ix_notifications_{column}", "notifications", [column])


def downgrade():
    op.drop_table("notifications")
    op.drop_table("meetings")
    op.drop_table("obligations")
