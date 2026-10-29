"""id orders with certificate copies

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "id_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("number", sa.String(length=60), nullable=True),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("person_name", sa.String(length=300), nullable=True),
        sa.Column("position", sa.String(length=300), nullable=True),
        sa.Column("org_name", sa.String(length=500), nullable=True),
        sa.Column("object_name", sa.String(length=500), nullable=True),
        sa.Column("city", sa.String(length=200), nullable=True),
        sa.Column("signer_name", sa.String(length=300), nullable=True),
        sa.Column("signer_position", sa.String(length=300), nullable=True),
        sa.Column("extra", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_id_orders_project_id", "id_orders", ["project_id"])
    op.create_table(
        "id_order_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("id_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_kind", sa.String(length=30), nullable=False, server_default="certificate"),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_id_order_files_order_id", "id_order_files", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_id_order_files_order_id", table_name="id_order_files")
    op.drop_table("id_order_files")
    op.drop_index("ix_id_orders_project_id", table_name="id_orders")
    op.drop_table("id_orders")
