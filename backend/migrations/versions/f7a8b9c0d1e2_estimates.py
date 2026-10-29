"""estimates, estimate lines, KS acts

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "f7a8b9c0d1e2"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "estimates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("source_name", sa.String(length=500), nullable=True),
        sa.Column("total", sa.Numeric(18, 2), nullable=True),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=False, server_default="22"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_estimates_project_id", "estimates", ["project_id"])
    op.create_table(
        "estimate_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("estimate_id", sa.Integer(), sa.ForeignKey("estimates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("section", sa.String(length=500), nullable=True),
        sa.Column("position", sa.String(length=40), nullable=True),
        sa.Column("code", sa.String(length=200), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("total", sa.Numeric(18, 2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_estimate_lines_estimate_id", "estimate_lines", ["estimate_id"])
    op.create_table(
        "ks_acts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("estimate_id", sa.Integer(), sa.ForeignKey("estimates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("act_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("lines_json", sa.Text(), nullable=False),
        sa.Column("total_ex_vat", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ks_acts_estimate_id", "ks_acts", ["estimate_id"])
    op.add_column("work_log_entries", sa.Column("estimate_line_id", sa.Integer(), sa.ForeignKey("estimate_lines.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_work_log_entries_estimate_line_id", "work_log_entries", ["estimate_line_id"])


def downgrade() -> None:
    op.drop_index("ix_work_log_entries_estimate_line_id", table_name="work_log_entries")
    op.drop_column("work_log_entries", "estimate_line_id")
    op.drop_index("ix_ks_acts_estimate_id", table_name="ks_acts")
    op.drop_table("ks_acts")
    op.drop_index("ix_estimate_lines_estimate_id", table_name="estimate_lines")
    op.drop_table("estimate_lines")
    op.drop_index("ix_estimates_project_id", table_name="estimates")
    op.drop_table("estimates")
