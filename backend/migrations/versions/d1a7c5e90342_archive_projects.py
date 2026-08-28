"""archive projects without deleting their data

Revision ID: d1a7c5e90342
Revises: c9f4a2b7d611
"""
from alembic import op
import sqlalchemy as sa


revision = "d1a7c5e90342"
down_revision = "c9f4a2b7d611"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_projects_archived_at", "projects", ["archived_at"])


def downgrade():
    op.drop_index("ix_projects_archived_at", table_name="projects")
    op.drop_column("projects", "archived_at")
