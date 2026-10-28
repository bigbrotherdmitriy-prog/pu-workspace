"""rd sheets register

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rd_sheets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "volume_id", sa.Integer(),
            sa.ForeignKey("rd_volumes.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "project_id", sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("sheet_no", sa.String(length=40), nullable=True),
        sa.Column("sheet_title", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("text_source", sa.String(length=10), nullable=False),
        sa.Column("text_len", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("volume_id", "page_no", name="uq_rd_sheet_page"),
    )
    op.create_index("ix_rd_sheets_volume_id", "rd_sheets", ["volume_id"])
    op.create_index("ix_rd_sheets_project_id", "rd_sheets", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_rd_sheets_project_id", table_name="rd_sheets")
    op.drop_index("ix_rd_sheets_volume_id", table_name="rd_sheets")
    op.drop_table("rd_sheets")
