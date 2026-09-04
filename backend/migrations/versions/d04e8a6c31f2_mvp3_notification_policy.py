"""MVP3 notification timezone, quiet hours and escalation policy.

Revision ID: d04e8a6c31f2
Revises: c93b7f4a21d0
"""

from alembic import op
import sqlalchemy as sa


revision = "d04e8a6c31f2"
down_revision = "c93b7f4a21d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_policies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timezone", sa.String(100), server_default="Europe/Moscow", nullable=False),
        sa.Column("deadline_local_time", sa.Time(), server_default="09:00:00", nullable=False),
        sa.Column("quiet_start", sa.Time(), server_default="22:00:00", nullable=False),
        sa.Column("quiet_end", sa.Time(), server_default="07:00:00", nullable=False),
        sa.Column("escalation_delays", sa.JSON(), server_default=sa.text("'[0,60,240]'::json"), nullable=False),
        sa.Column("channels", sa.JSON(), server_default=sa.text("'[\"in_app\"]'::json"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("record_version > 0", name="ck_notification_policy_record_version"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_notification_policy_project_user"),
    )
    for column in ("organization_id", "project_id", "user_id"):
        op.create_index(f"ix_notification_policies_{column}", "notification_policies", [column])


def downgrade():
    op.drop_table("notification_policies")
