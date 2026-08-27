"""add MVP4 execution and finance

Revision ID: f42a81c9d3e6
Revises: e91f05b728c4
"""
from alembic import op
import sqlalchemy as sa

revision = "f42a81c9d3e6"
down_revision = "e91f05b728c4"
branch_labels = None
depends_on = None


def _indexes(table, columns):
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade():
    op.create_table("schedule_baselines",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"), sa.Column("note", sa.Text()),
        sa.Column("approved_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "version", name="uq_schedule_baseline_version"))
    _indexes("schedule_baselines", ("project_id", "status"))
    op.create_table("schedule_items",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baseline_id", sa.Integer(), sa.ForeignKey("schedule_baselines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False), sa.Column("planned_start", sa.Date()), sa.Column("planned_finish", sa.Date()),
        sa.Column("actual_start", sa.Date()), sa.Column("actual_finish", sa.Date()),
        sa.Column("planned_progress", sa.Float(), nullable=False, server_default="0"), sa.Column("actual_progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("source_name", sa.String(1000)), sa.Column("source_excerpt", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    _indexes("schedule_items", ("project_id", "baseline_id", "planned_finish", "status"))
    op.create_table("budget_lines",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL")),
        sa.Column("category", sa.String(200), nullable=False), sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("planned_amount", sa.Numeric(18,2), nullable=False, server_default="0"), sa.Column("committed_amount", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("actual_amount", sa.Numeric(18,2), nullable=False, server_default="0"), sa.Column("forecast_amount", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"), sa.Column("status", sa.String(30), nullable=False, server_default="proposed"),
        sa.Column("source_name", sa.String(1000)), sa.Column("source_excerpt", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    _indexes("budget_lines", ("project_id", "contract_id", "category", "status"))
    op.create_table("cash_flow_entries",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL")), sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("title", sa.String(500), nullable=False), sa.Column("planned_date", sa.Date(), nullable=False), sa.Column("actual_date", sa.Date()),
        sa.Column("planned_amount", sa.Numeric(18,2), nullable=False), sa.Column("actual_amount", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("counterparty", sa.String(500)), sa.Column("status", sa.String(30), nullable=False, server_default="proposed"),
        sa.Column("source_name", sa.String(1000)), sa.Column("source_excerpt", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    _indexes("cash_flow_entries", ("project_id", "contract_id", "direction", "planned_date", "status"))
    op.create_table("procurement_items",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL")), sa.Column("title", sa.String(500), nullable=False),
        sa.Column("supplier", sa.String(500)), sa.Column("stage", sa.String(30), nullable=False, server_default="request"),
        sa.Column("planned_delivery", sa.Date()), sa.Column("actual_delivery", sa.Date()),
        sa.Column("planned_amount", sa.Numeric(18,2), nullable=False, server_default="0"), sa.Column("actual_amount", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("source_name", sa.String(1000)), sa.Column("source_excerpt", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    _indexes("procurement_items", ("project_id", "contract_id", "stage", "planned_delivery"))
    op.create_table("acceptance_acts",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL")), sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("number", sa.String(200), nullable=False), sa.Column("title", sa.String(500), nullable=False), sa.Column("act_date", sa.Date()),
        sa.Column("amount", sa.Numeric(18,2), nullable=False, server_default="0"), sa.Column("status", sa.String(30), nullable=False, server_default="proposed"),
        sa.Column("source_name", sa.String(1000)), sa.Column("source_excerpt", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    _indexes("acceptance_acts", ("project_id", "contract_id", "document_id", "act_date", "status"))


def downgrade():
    for table in ("acceptance_acts", "procurement_items", "cash_flow_entries", "budget_lines", "schedule_items", "schedule_baselines"):
        op.drop_table(table)
