"""Add batch link receipts only; no money, dates, statuses or links are backfilled."""
from alembic import op
import sqlalchemy as sa

revision = "d021a6c0b002"
down_revision = "d021a6c0b001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dds_budget_link_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_sha256", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("budget_period", sa.Integer(), nullable=False),
        sa.Column("budget_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_dds_budget_link_operation_key"),
        sa.CheckConstraint("budget_revision > 0", name="ck_dds_budget_link_revision"),
    )
    op.create_index("ix_dds_budget_link_operations_project_id", "dds_budget_link_operations", ["project_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM dds_budget_link_operations")):
        raise RuntimeError("DDS_BUDGET_LINK_DOWNGRADE_BLOCKED: preserve applied and undone link history")
    op.drop_table("dds_budget_link_operations")
