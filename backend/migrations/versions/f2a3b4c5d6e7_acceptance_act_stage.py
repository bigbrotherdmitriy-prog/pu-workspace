"""Add stage link, generation flags and cash-flow link to acceptance_acts."""

from alembic import op
import sqlalchemy as sa


revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "acceptance_acts",
        sa.Column("stage_id", sa.Integer(), sa.ForeignKey("contract_stages.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "acceptance_acts",
        sa.Column("generated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "acceptance_acts",
        sa.Column("quantity", sa.Numeric(18, 5), nullable=True),
    )
    op.add_column(
        "acceptance_acts",
        sa.Column("vat_amount", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "acceptance_acts",
        sa.Column("cash_flow_entry_id", sa.Integer(), sa.ForeignKey("cash_flow_entries.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_acceptance_acts_stage_id", "acceptance_acts", ["stage_id"])


def downgrade():
    op.drop_index("ix_acceptance_acts_stage_id", table_name="acceptance_acts")
    op.drop_column("acceptance_acts", "cash_flow_entry_id")
    op.drop_column("acceptance_acts", "vat_amount")
    op.drop_column("acceptance_acts", "quantity")
    op.drop_column("acceptance_acts", "generated")
    op.drop_column("acceptance_acts", "stage_id")
