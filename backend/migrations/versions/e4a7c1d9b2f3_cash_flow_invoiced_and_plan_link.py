"""Confirmed invoices are fact: invoiced_amount and a link to the plan DDS line."""

from alembic import op
import sqlalchemy as sa


revision = "e4a7c1d9b2f3"
down_revision = "c2b8d4e6f7a9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cash_flow_entries",
        sa.Column("invoiced_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )
    op.add_column("cash_flow_entries", sa.Column("plan_entry_id", sa.Integer(), nullable=True))
    op.create_index("ix_cash_flow_entries_plan_entry_id", "cash_flow_entries", ["plan_entry_id"])
    if op.get_context().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_cash_flow_entries_plan_entry_id", "cash_flow_entries", "cash_flow_entries",
            ["plan_entry_id"], ["id"], ondelete="SET NULL",
        )


def downgrade():
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_cash_flow_entries_plan_entry_id", "cash_flow_entries", type_="foreignkey")
    op.drop_index("ix_cash_flow_entries_plan_entry_id", table_name="cash_flow_entries")
    with op.batch_alter_table("cash_flow_entries") as batch:
        batch.drop_column("plan_entry_id")
        batch.drop_column("invoiced_amount")
