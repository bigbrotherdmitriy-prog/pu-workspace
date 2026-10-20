"""id participants

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
Create Date: 2026-02-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "a7b8c9d0e1f2"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "id_participants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("object_name", sa.String(length=300), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("position", sa.String(length=300), nullable=True),
        sa.Column("full_name", sa.String(length=300), nullable=False),
        sa.Column("order_ref", sa.String(length=500), nullable=True),
        sa.Column("nrs_id", sa.String(length=100), nullable=True),
        sa.Column("org_name", sa.String(length=500), nullable=True),
        sa.Column("org_ogrn", sa.String(length=20), nullable=True),
        sa.Column("org_inn", sa.String(length=12), nullable=True),
        sa.Column("org_address", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_id_participants_project_id", "id_participants", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_id_participants_project_id", table_name="id_participants")
    op.drop_table("id_participants")
