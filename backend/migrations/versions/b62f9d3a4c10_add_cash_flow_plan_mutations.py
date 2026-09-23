"""Add CAS/idempotency receipts for proposed DDS plan edits.

Revision ID: b62f9d3a4c10
Revises: c31a7b9d2e40
"""

from alembic import op
import sqlalchemy as sa


revision = "b62f9d3a4c10"
down_revision = "c31a7b9d2e40"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cash_flow_entries",
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_table(
        "cash_flow_plan_mutations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("cash_flow_entry_id", sa.Integer(), nullable=False),
        sa.Column("result_cash_flow_entry_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("expected_record_version", sa.Integer(), nullable=False),
        sa.Column("resulting_record_version", sa.Integer(), nullable=False),
        sa.Column("previous_planned_date", sa.Date(), nullable=False),
        sa.Column("previous_planned_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("planned_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cash_flow_entry_id"], ["cash_flow_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_cash_flow_entry_id"], ["cash_flow_entries.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("cash_flow_entry_id", "idempotency_key", name="uq_cash_flow_plan_mutation_key"),
        sa.CheckConstraint("operation IN ('edit','move','copy')", name="ck_cash_flow_plan_mutation_operation"),
    )
    op.create_index("ix_cash_flow_plan_mutations_project_id", "cash_flow_plan_mutations", ["project_id"])
    op.create_index("ix_cash_flow_plan_mutations_cash_flow_entry_id", "cash_flow_plan_mutations", ["cash_flow_entry_id"])
    op.create_index("ix_cash_flow_plan_mutations_result_cash_flow_entry_id", "cash_flow_plan_mutations", ["result_cash_flow_entry_id"])


def downgrade():
    op.drop_table("cash_flow_plan_mutations")
    op.drop_column("cash_flow_entries", "record_version")
