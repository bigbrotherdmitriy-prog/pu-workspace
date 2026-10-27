"""rd volumes and spec items

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-02-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rd_volumes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("object_name", sa.String(length=300), nullable=True),
        sa.Column("section", sa.String(length=60), nullable=True),
        sa.Column("code", sa.String(length=200), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("spec_pages", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "provider", "external_id", name="uq_rd_volume_source"),
    )
    op.create_index("ix_rd_volumes_project_id", "rd_volumes", ["project_id"])

    op.create_table(
        "rd_spec_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("volume_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pos", sa.String(length=40), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type_mark", sa.String(length=500), nullable=True),
        sa.Column("code", sa.String(length=200), nullable=True),
        sa.Column("manufacturer", sa.String(length=300), nullable=True),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column("qty", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["volume_id"], ["rd_volumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rd_spec_items_volume_id", "rd_spec_items", ["volume_id"])
    op.create_index("ix_rd_spec_items_project_id", "rd_spec_items", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_rd_spec_items_project_id", table_name="rd_spec_items")
    op.drop_index("ix_rd_spec_items_volume_id", table_name="rd_spec_items")
    op.drop_table("rd_spec_items")
    op.drop_index("ix_rd_volumes_project_id", table_name="rd_volumes")
    op.drop_table("rd_volumes")
