"""Add user_device_tokens for FCM push (V6-04)."""

from alembic import op
import sqlalchemy as sa


revision = "1deff6634d0d"
down_revision = "f2da35389553"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.String(100), nullable=True),
        sa.Column("fcm_token", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False, server_default="android"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("platform IN ('android')", name="ck_user_device_tokens_platform"),
        sa.UniqueConstraint("fcm_token", name="uq_user_device_tokens_fcm_token"),
    )
    op.create_index("ix_user_device_tokens_user_id", "user_device_tokens", ["user_id"])


def downgrade():
    # Device tokens are re-registered by the app on its next start, so the
    # table holds no data that cannot be recreated.
    op.drop_index("ix_user_device_tokens_user_id", table_name="user_device_tokens")
    op.drop_table("user_device_tokens")
