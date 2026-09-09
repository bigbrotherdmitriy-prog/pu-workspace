"""Add the complete immutable metadata envelope to virtual snapshot nodes."""

from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a23"
down_revision = "a54f001c0a22"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("virtual_nodes", sa.Column("source_path", sa.Text(), nullable=True))
    op.add_column("virtual_nodes", sa.Column("parent_external_ids", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("virtual_nodes", sa.Column("provider_revision", sa.String(length=500), nullable=True))
    op.add_column("virtual_nodes", sa.Column("provider_metadata_hash", sa.String(length=64), nullable=False,
                                              server_default="unknown"))
    op.add_column("virtual_nodes", sa.Column("web_url", sa.Text(), nullable=True))
    op.add_column("virtual_nodes", sa.Column("availability", sa.String(length=30), nullable=False,
                                              server_default="unknown"))
    op.add_column("virtual_nodes", sa.Column("acl_state", sa.String(length=30), nullable=False,
                                              server_default="unknown"))
    op.add_column("virtual_nodes", sa.Column("analysis_state", sa.String(length=30), nullable=False,
                                              server_default="pending"))
    op.create_index("ix_virtual_nodes_analysis_state", "virtual_nodes", ["analysis_state"])


def downgrade():
    op.drop_index("ix_virtual_nodes_analysis_state", table_name="virtual_nodes")
    for column in ("analysis_state", "acl_state", "availability", "web_url", "provider_metadata_hash",
                   "provider_revision", "parent_external_ids", "source_path"):
        op.drop_column("virtual_nodes", column)
