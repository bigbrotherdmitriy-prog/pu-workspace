"""invoice lines

Revision ID: f3a4b5c6d7e8
Revises: d0e1f2a3b4c5
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "f3a4b5c6d7e8"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id", sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "cash_flow_entry_id", sa.Integer(),
            sa.ForeignKey("cash_flow_entries.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=True),
        sa.Column(
            "purchase_item_id", sa.Integer(),
            sa.ForeignKey("purchase_items.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "match_status", sa.String(length=20), nullable=False,
            server_default="none",
        ),
        sa.Column("match_score", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_invoice_lines_project_id", "invoice_lines", ["project_id"],
    )
    op.create_index(
        "ix_invoice_lines_cash_flow_entry_id", "invoice_lines",
        ["cash_flow_entry_id"],
    )
    op.create_index(
        "ix_invoice_lines_purchase_item_id", "invoice_lines", ["purchase_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_lines_purchase_item_id", table_name="invoice_lines")
    op.drop_index("ix_invoice_lines_cash_flow_entry_id", table_name="invoice_lines")
    op.drop_index("ix_invoice_lines_project_id", table_name="invoice_lines")
    op.drop_table("invoice_lines")
