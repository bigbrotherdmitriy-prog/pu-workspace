"""add organizer source recheck metadata

Revision ID: c82a4e5f6b71
Revises: b71f1a2c3d40
"""
from alembic import op
import sqlalchemy as sa

revision = "c82a4e5f6b71"
down_revision = "b71f1a2c3d40"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizer_actions", sa.Column("source_modified_at", sa.String(100)))
    op.add_column("organizer_actions", sa.Column("source_checksum", sa.String(128)))


def downgrade():
    op.drop_column("organizer_actions", "source_checksum")
    op.drop_column("organizer_actions", "source_modified_at")
