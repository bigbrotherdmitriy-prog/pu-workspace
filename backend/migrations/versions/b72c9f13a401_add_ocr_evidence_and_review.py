"""add OCR evidence and review state

Revision ID: b72c9f13a401
Revises: a31c7d8e9f20
"""
from alembic import op
import sqlalchemy as sa


revision = "b72c9f13a401"
down_revision = "a31c7d8e9f20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("ocr_confidence", sa.Float()))
    op.add_column(
        "documents",
        sa.Column("ocr_review_status", sa.String(30), nullable=False, server_default="not_required"),
    )
    op.add_column("documents", sa.Column("ocr_metadata", sa.JSON()))
    op.create_index("ix_documents_ocr_review_status", "documents", ["ocr_review_status"])


def downgrade():
    op.drop_index("ix_documents_ocr_review_status", table_name="documents")
    op.drop_column("documents", "ocr_metadata")
    op.drop_column("documents", "ocr_review_status")
    op.drop_column("documents", "ocr_confidence")
