"""add primary source folder marker

Revision ID: e04c6a718d93
Revises: d93b5f607c82
"""
from alembic import op
import sqlalchemy as sa

revision = "e04c6a718d93"
down_revision = "d93b5f607c82"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("source_folders", sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_source_folders_is_primary", "source_folders", ["is_primary"])
    op.execute("""
        UPDATE source_folders sf SET is_primary=true
        WHERE sf.id IN (SELECT min(id) FROM source_folders GROUP BY project_id)
    """)


def downgrade():
    op.drop_index("ix_source_folders_is_primary", table_name="source_folders")
    op.drop_column("source_folders", "is_primary")
