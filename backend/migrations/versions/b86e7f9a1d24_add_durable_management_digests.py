"""Add durable project/user management digest preferences and receipts.

Revision ID: b86e7f9a1d24
Revises: b85e7f9a1d23
"""
from alembic import op
import sqlalchemy as sa


revision = "b86e7f9a1d24"
down_revision = "b85e7f9a1d23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_policies", sa.Column(
        "digest_enabled", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    op.add_column("notification_policies", sa.Column(
        "digest_cadence", sa.String(20), nullable=False, server_default="daily",
    ))
    op.add_column("notification_policies", sa.Column(
        "digest_local_time", sa.Time(), nullable=False, server_default="09:00:00",
    ))
    op.create_check_constraint(
        "ck_notification_policies_digest_cadence", "notification_policies",
        "digest_cadence IN ('daily','weekdays')",
    )

    op.create_table(
        "management_digests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", sa.Integer(), sa.ForeignKey("notification_policies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("policy_record_version", sa.Integer(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("notification_id", sa.Integer(), sa.ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("item_refs", sa.JSON(), nullable=False),
        sa.Column("requested_channels", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("policy_id", "local_date", name="uq_management_digest_policy_day"),
        sa.CheckConstraint("policy_record_version > 0", name="ck_management_digest_policy_version"),
        sa.CheckConstraint("item_count >= 0 AND item_count <= 100", name="ck_management_digest_item_count"),
    )
    for column in ("organization_id", "project_id", "user_id", "policy_id", "local_date"):
        op.create_index(f"ix_management_digests_{column}", "management_digests", [column])


def downgrade() -> None:
    op.drop_table("management_digests")
    op.drop_constraint("ck_notification_policies_digest_cadence", "notification_policies", type_="check")
    op.drop_column("notification_policies", "digest_local_time")
    op.drop_column("notification_policies", "digest_cadence")
    op.drop_column("notification_policies", "digest_enabled")
