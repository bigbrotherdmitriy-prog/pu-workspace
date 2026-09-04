"""add cash flow dimensions for DDS views

Revision ID: c24d9e7a61b0
Revises: a31c7d8e9f20
"""
from alembic import op
import sqlalchemy as sa

revision = "c24d9e7a61b0"
down_revision = "a31c7d8e9f20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cash_flow_entries", sa.Column("object_name", sa.String(300), nullable=True))
    op.add_column("cash_flow_entries", sa.Column("category", sa.String(200), nullable=True))
    op.add_column("cash_flow_entries", sa.Column("note", sa.Text(), nullable=True))
    op.create_index("ix_cash_flow_entries_object_name", "cash_flow_entries", ["object_name"])
    op.create_index("ix_cash_flow_entries_category", "cash_flow_entries", ["category"])


def downgrade():
    op.drop_index("ix_cash_flow_entries_category", table_name="cash_flow_entries")
    op.drop_index("ix_cash_flow_entries_object_name", table_name="cash_flow_entries")
    op.drop_column("cash_flow_entries", "note")
    op.drop_column("cash_flow_entries", "category")
    op.drop_column("cash_flow_entries", "object_name")
