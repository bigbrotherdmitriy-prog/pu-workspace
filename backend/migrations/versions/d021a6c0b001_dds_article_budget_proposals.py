"""Add explicit analytical budget scopes and immutable import receipts; no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "d021a6c0b001"
down_revision = "c70a0090f001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dds_article_budget_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_sha256", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("budget_period", sa.Integer(), nullable=False),
        sa.Column("budget_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(60), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_dds_article_operation_key"),
        sa.UniqueConstraint("project_id", "contract_id", "source_document_version_id", "mode", "budget_period", "budget_revision",
                            name="uq_dds_article_operation_source"),
    )
    op.create_index("ix_dds_article_budget_operations_project_id", "dds_article_budget_operations", ["project_id"])
    op.add_column("budget_lines", sa.Column("line_kind", sa.String(30), server_default="legacy_unclassified", nullable=False))
    op.add_column("budget_lines", sa.Column("budget_period", sa.Integer()))
    op.add_column("budget_lines", sa.Column("budget_revision", sa.Integer()))
    op.add_column("budget_lines", sa.Column("article_normalized_name", sa.String(1000)))
    op.add_column("budget_lines", sa.Column("record_version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("cash_flow_entries", sa.Column("entry_kind", sa.String(30), server_default="legacy_unclassified", nullable=False))
    op.add_column("cash_flow_entries", sa.Column("matrix_article_id", sa.Integer()))
    op.add_column("cash_flow_entries", sa.Column("matrix_month", sa.Integer()))
    op.add_column("cash_flow_entries", sa.Column("matrix_operation_id", sa.Integer(), sa.ForeignKey("dds_article_budget_operations.id", ondelete="RESTRICT")))
    # Only additive metadata/defaults: no UPDATE of amounts, links, dates or statuses.


def downgrade():
    # Old applications cannot interpret analytical types/provenance/receipts safely.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM dds_article_budget_operations")):
        raise RuntimeError("DDS_ARTICLE_DOWNGRADE_BLOCKED: receipts/history require a compatible rollback build")
    if connection.scalar(sa.text("SELECT count(*) FROM budget_lines WHERE line_kind <> 'legacy_unclassified'")):
        raise RuntimeError("DDS_ARTICLE_DOWNGRADE_BLOCKED: typed budgets require a compatible rollback build")
    if connection.scalar(sa.text("SELECT count(*) FROM cash_flow_entries WHERE entry_kind <> 'legacy_unclassified'")):
        raise RuntimeError("DDS_ARTICLE_DOWNGRADE_BLOCKED: typed cash flow requires a compatible rollback build")
    for column in ("matrix_operation_id", "matrix_month", "matrix_article_id", "entry_kind"):
        op.drop_column("cash_flow_entries", column)
    for column in ("record_version", "article_normalized_name", "budget_revision", "budget_period", "line_kind"):
        op.drop_column("budget_lines", column)
    op.drop_table("dds_article_budget_operations")
