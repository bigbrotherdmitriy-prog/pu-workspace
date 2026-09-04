"""link invoice payments to control registers

Revision ID: d8b2f14c7a30
Revises: d1a7c5e90342
"""
from alembic import op
import sqlalchemy as sa

revision = "d8b2f14c7a30"
down_revision = "d1a7c5e90342"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cash_flow_entries", sa.Column("schedule_item_id", sa.Integer(), nullable=True))
    op.add_column("cash_flow_entries", sa.Column("budget_line_id", sa.Integer(), nullable=True))
    op.add_column("cash_flow_entries", sa.Column("source_document_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_cash_flow_schedule_item", "cash_flow_entries", "schedule_items", ["schedule_item_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_cash_flow_budget_line", "cash_flow_entries", "budget_lines", ["budget_line_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_cash_flow_source_document", "cash_flow_entries", "documents", ["source_document_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_cash_flow_entries_schedule_item_id", "cash_flow_entries", ["schedule_item_id"])
    op.create_index("ix_cash_flow_entries_budget_line_id", "cash_flow_entries", ["budget_line_id"])
    op.create_index("ix_cash_flow_entries_source_document_id", "cash_flow_entries", ["source_document_id"])


def downgrade():
    op.drop_index("ix_cash_flow_entries_source_document_id", table_name="cash_flow_entries")
    op.drop_index("ix_cash_flow_entries_budget_line_id", table_name="cash_flow_entries")
    op.drop_index("ix_cash_flow_entries_schedule_item_id", table_name="cash_flow_entries")
    op.drop_constraint("fk_cash_flow_source_document", "cash_flow_entries", type_="foreignkey")
    op.drop_constraint("fk_cash_flow_budget_line", "cash_flow_entries", type_="foreignkey")
    op.drop_constraint("fk_cash_flow_schedule_item", "cash_flow_entries", type_="foreignkey")
    op.drop_column("cash_flow_entries", "source_document_id")
    op.drop_column("cash_flow_entries", "budget_line_id")
    op.drop_column("cash_flow_entries", "schedule_item_id")
