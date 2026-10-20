"""Add dds_plan_versions table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a4b5c6d7e8f9"
down_revision = "d3e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dds_plan_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("totals", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.UniqueConstraint("project_id", "contract_id", "version_number", name="uq_dds_plan_version_number"),
    )
    op.create_index("ix_dds_plan_versions_project_id", "dds_plan_versions", ["project_id"])


def downgrade():
    op.drop_index("ix_dds_plan_versions_project_id", table_name="dds_plan_versions")
    op.drop_table("dds_plan_versions")
