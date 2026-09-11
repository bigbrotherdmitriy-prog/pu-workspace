"""add snapshot metadata envelope

Revision ID: 21d8f9354c18
Revises: e16a1c2d3f40
Create Date: 2026-09-11 02:53:12.109640
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '21d8f9354c18'
down_revision: Union[str, Sequence[str], None] = 'e16a1c2d3f40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_virtual_nodes_analysis_state", table_name="virtual_nodes")
    for column in ("analysis_state", "acl_state", "availability", "web_url", "provider_metadata_hash",
                   "provider_revision", "parent_external_ids", "source_path"):
        op.drop_column("virtual_nodes", column)
