"""Add manager-confirmed contract budget proposals.

Revision ID: e26b8d0f3c51
Revises: d15a7c9e2b40
"""

from alembic import op
import sqlalchemy as sa


revision = "e26b8d0f3c51"
down_revision = "d15a7c9e2b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contract_budget_proposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("contract_record_version", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("advance_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("retention_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="RUB"),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("selected_cost_category_id", sa.Integer(), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("source_document_version_id", sa.Integer(), nullable=True),
        sa.Column("source_document_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_name", sa.String(length=1000), nullable=True),
        sa.Column("target_budget_line_id", sa.Integer(), nullable=True),
        sa.Column("created_budget_line_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="proposed"),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("operation IN ('create','revise')", name="ck_contract_budget_proposal_operation"),
        sa.CheckConstraint("status IN ('proposed','confirmed','rejected','superseded')", name="ck_contract_budget_proposal_status"),
        sa.CheckConstraint(
            "(source_document_id IS NULL AND source_document_version_id IS NULL AND source_document_sha256 IS NULL) OR "
            "(source_document_id IS NOT NULL AND source_document_version_id IS NOT NULL AND source_document_sha256 IS NOT NULL)",
            name="ck_contract_budget_proposal_source_pin",
        ),
        sa.UniqueConstraint("contract_id", "contract_record_version", name="uq_contract_budget_proposal_version"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_cost_category_id"], ["cost_categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_document_version_id"], ["document_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_budget_line_id"], ["budget_lines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_budget_line_id"], ["budget_lines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    for column in (
        "project_id", "contract_id", "selected_cost_category_id", "source_document_id",
        "source_document_version_id", "target_budget_line_id", "created_budget_line_id", "status",
    ):
        op.create_index(f"ix_contract_budget_proposals_{column}", "contract_budget_proposals", [column])


def downgrade() -> None:
    op.drop_table("contract_budget_proposals")
