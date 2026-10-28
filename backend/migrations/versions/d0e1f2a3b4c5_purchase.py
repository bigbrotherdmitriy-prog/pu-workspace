"""purchase items

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id", sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("group_key", sa.String(length=600), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type_mark", sa.String(length=500), nullable=True),
        sa.Column("manufacturer", sa.String(length=300), nullable=True),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column(
            "qty_needed", sa.Numeric(18, 3), nullable=False, server_default="0",
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="need",
        ),
        sa.Column(
            "qty_ordered", sa.Numeric(18, 3), nullable=False, server_default="0",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("project_id", "group_key", name="uq_purchase_item_group"),
    )
    op.create_index("ix_purchase_items_project_id", "purchase_items", ["project_id"])

    op.create_table(
        "purchase_item_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "item_id", sa.Integer(),
            sa.ForeignKey("purchase_items.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "spec_item_id", sa.Integer(),
            sa.ForeignKey("rd_spec_items.id", ondelete="CASCADE"), nullable=False,
            unique=True,
        ),
        sa.Column("qty", sa.Numeric(18, 3), nullable=True),
    )
    op.create_index(
        "ix_purchase_item_sources_item_id", "purchase_item_sources", ["item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_purchase_item_sources_item_id", table_name="purchase_item_sources",
    )
    op.drop_table("purchase_item_sources")
    op.drop_index("ix_purchase_items_project_id", table_name="purchase_items")
    op.drop_table("purchase_items")
