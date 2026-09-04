"""add Microsoft Project schedule sources

Revision ID: c05e2f3a4b56
Revises: c04d1e2f3a45
"""
from alembic import op
import sqlalchemy as sa

revision = "c05e2f3a4b56"
down_revision = "c04d1e2f3a45"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("schedule_baselines", sa.Column("source_format", sa.String(30), nullable=True))
    op.add_column("schedule_baselines", sa.Column("source_sha256", sa.String(64), nullable=True))
    op.create_index("ix_schedule_baselines_source_sha256", "schedule_baselines", ["source_sha256"])


def downgrade():
    op.drop_index("ix_schedule_baselines_source_sha256", table_name="schedule_baselines")
    op.drop_column("schedule_baselines", "source_sha256")
    op.drop_column("schedule_baselines", "source_format")
