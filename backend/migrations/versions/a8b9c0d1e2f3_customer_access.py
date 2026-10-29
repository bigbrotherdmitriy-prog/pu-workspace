"""customer access and customer review of KS acts

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_access",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invited_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", name="uq_customer_access"),
    )
    op.create_index("ix_customer_access_user_id", "customer_access", ["user_id"])
    op.add_column("ks_acts", sa.Column("header_json", sa.Text(), nullable=True))
    op.add_column("ks_acts", sa.Column("customer_status", sa.String(length=20), nullable=False, server_default="pending"))
    op.add_column("ks_acts", sa.Column("customer_comment", sa.Text(), nullable=True))
    op.add_column("ks_acts", sa.Column("customer_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ks_acts", sa.Column("customer_decided_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    op.drop_column("ks_acts", "customer_decided_by")
    op.drop_column("ks_acts", "customer_decided_at")
    op.drop_column("ks_acts", "customer_comment")
    op.drop_column("ks_acts", "customer_status")
    op.drop_column("ks_acts", "header_json")
    op.drop_index("ix_customer_access_user_id", table_name="customer_access")
    op.drop_table("customer_access")
