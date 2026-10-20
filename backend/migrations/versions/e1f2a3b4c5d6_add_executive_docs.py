"""Add executive docs tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e1f2a3b4c5d6"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "id_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("object_name", sa.String(300), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("folder_id", sa.String(500), nullable=False),
        sa.Column("folder_name", sa.String(500), nullable=True),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_report",
            sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "provider", "folder_id", name="uq_id_source_folder"),
    )
    op.create_index("ix_id_sources_project_id", "id_sources", ["project_id"])

    op.create_table(
        "id_requirements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("object_name", sa.String(300), nullable=True),
        sa.Column("section", sa.String(60), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("doc_kind", sa.String(30), nullable=False),
        sa.Column("copies", sa.Integer(), nullable=True),
        sa.Column("required_if_applicable", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("norm_ref", sa.String(200), nullable=True),
        sa.Column("evidence_quote", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_id_requirements_project_id", "id_requirements", ["project_id"])
    op.create_index("ix_id_requirements_contract_id", "id_requirements", ["contract_id"])

    op.create_table(
        "id_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(300), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(200), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("section", sa.String(60), nullable=True),
        sa.Column("object_name", sa.String(300), nullable=True),
        sa.Column(
            "issues",
            sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "contained_kinds",
            sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql"),
            nullable=True,
        ),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("id_requirements.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("id_sources.id", ondelete="CASCADE"), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "provider", "external_id", name="uq_id_file_external"),
    )
    op.create_index("ix_id_files_project_id", "id_files", ["project_id"])
    op.create_index("ix_id_files_external_id", "id_files", ["external_id"])
    op.create_index("ix_id_files_source_id", "id_files", ["source_id"])


def downgrade():
    op.drop_index("ix_id_files_source_id", table_name="id_files")
    op.drop_index("ix_id_files_external_id", table_name="id_files")
    op.drop_index("ix_id_files_project_id", table_name="id_files")
    op.drop_table("id_files")

    op.drop_index("ix_id_requirements_contract_id", table_name="id_requirements")
    op.drop_index("ix_id_requirements_project_id", table_name="id_requirements")
    op.drop_table("id_requirements")

    op.drop_index("ix_id_sources_project_id", table_name="id_sources")
    op.drop_table("id_sources")
