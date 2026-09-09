"""Preserve Google Drive shortcut targets in immutable virtual nodes."""

from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a24"
down_revision = "a54f001c0a23"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("virtual_nodes", sa.Column("shortcut_target_id", sa.String(length=255), nullable=True))
    op.add_column("virtual_nodes", sa.Column("shortcut_target_mime_type", sa.String(length=255), nullable=True))
    op.add_column("virtual_nodes", sa.Column("shortcut_target_resource_key", sa.String(length=500), nullable=True))


def downgrade():
    op.drop_column("virtual_nodes", "shortcut_target_resource_key")
    op.drop_column("virtual_nodes", "shortcut_target_mime_type")
    op.drop_column("virtual_nodes", "shortcut_target_id")
