"""add contract document package links

Revision ID: e31a7b8c9d02
Revises: d20f6a7b8c91
"""
from alembic import op
import sqlalchemy as sa

revision = "e31a7b8c9d02"
down_revision = "d20f6a7b8c91"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contract_document_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(30), nullable=False, server_default="application"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("contract_id", "document_id", name="uq_contract_document_link"),
    )
    op.create_index("ix_contract_document_links_project_id", "contract_document_links", ["project_id"])
    op.create_index("ix_contract_document_links_contract_id", "contract_document_links", ["contract_id"])
    op.create_index("ix_contract_document_links_document_id", "contract_document_links", ["document_id"])


def downgrade():
    op.drop_table("contract_document_links")
