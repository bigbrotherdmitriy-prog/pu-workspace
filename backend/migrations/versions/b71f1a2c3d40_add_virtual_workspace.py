"""add virtual workspace snapshot entities

Revision ID: b71f1a2c3d40
Revises: a69e8b024f10
"""
from alembic import op
import sqlalchemy as sa

revision = "b71f1a2c3d40"
down_revision = "a69e8b024f10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_folders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="google_drive"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "external_id", name="uq_source_folder_project_external"),
    )
    op.create_index("ix_source_folders_project_id", "source_folders", ["project_id"])
    op.create_table(
        "workspace_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_folder_id", sa.Integer(), sa.ForeignKey("source_folders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="building"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_workspace_snapshots_project_id", "workspace_snapshots", ["project_id"])
    op.create_index("ix_workspace_snapshots_source_folder_id", "workspace_snapshots", ["source_folder_id"])
    op.create_index("ix_workspace_snapshots_status", "workspace_snapshots", ["status"])
    op.create_table(
        "virtual_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("workspace_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("parent_external_id", sa.String(255)),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("node_type", sa.String(20), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("checksum", sa.String(128)),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("snapshot_id", "external_id", name="uq_virtual_node_snapshot_external"),
    )
    op.create_index("ix_virtual_nodes_snapshot_id", "virtual_nodes", ["snapshot_id"])
    op.create_index("ix_virtual_nodes_parent_external_id", "virtual_nodes", ["parent_external_id"])
    op.create_index("ix_virtual_nodes_node_type", "virtual_nodes", ["node_type"])
    op.create_table(
        "extraction_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("extractor", sa.String(100), nullable=False),
        sa.Column("text_content", sa.Text()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_extraction_results_document_version_id", "extraction_results", ["document_version_id"])
    op.create_index("ix_extraction_results_status", "extraction_results", ["status"])


def downgrade():
    op.drop_table("extraction_results")
    op.drop_table("virtual_nodes")
    op.drop_table("workspace_snapshots")
    op.drop_table("source_folders")
