"""Add contract_stages table."""

from alembic import op
import sqlalchemy as sa


revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contract_stages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_name", sa.String(300), nullable=True),
        sa.Column("stage_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("planned_finish", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("vat_included", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=True),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), server_default="proposed", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("contract_id", "object_name", "stage_number", name="uq_contract_stage_number"),
    )
    op.create_index("ix_contract_stages_project_id", "contract_stages", ["project_id"])
    op.create_index("ix_contract_stages_contract_id", "contract_stages", ["contract_id"])


def downgrade():
    op.drop_index("ix_contract_stages_contract_id", table_name="contract_stages")
    op.drop_index("ix_contract_stages_project_id", table_name="contract_stages")
    op.drop_table("contract_stages")
