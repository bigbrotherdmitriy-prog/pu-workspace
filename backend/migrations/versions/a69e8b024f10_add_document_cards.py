"""add document card metadata

Revision ID: a69e8b024f10
Revises: f58c6d490c71
"""
from alembic import op
import sqlalchemy as sa

revision = "a69e8b024f10"
down_revision = "f58c6d490c71"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("current_version", sa.Integer(), server_default="1", nullable=False))
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])


def downgrade():
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_column("documents", "current_version")
    op.drop_column("documents", "summary")
    op.drop_column("documents", "source_modified_at")
    op.drop_column("documents", "content_hash")
