"""add document OCR metadata

Revision ID: a31c7d8e9f20
Revises: f42b8c9d0e13
"""
from alembic import op
import sqlalchemy as sa

revision = "a31c7d8e9f20"
down_revision = "f42b8c9d0e13"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("extraction_method", sa.String(30)))
    op.add_column("documents", sa.Column("extraction_quality", sa.String(30)))
    op.add_column("documents", sa.Column("ocr_pages", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("ocr_updated_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("documents", "ocr_updated_at")
    op.drop_column("documents", "ocr_pages")
    op.drop_column("documents", "extraction_quality")
    op.drop_column("documents", "extraction_method")
