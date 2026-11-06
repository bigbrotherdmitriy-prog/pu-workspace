"""Proposal ONLY: reduced-scope material_receipts table.

Inert Alembic-style proposal kept outside active migration revisions.
Not imported or executed by the application. No data backfill, no
environment or database calls. Activation requires refreshing
down_revision and manual review.
"""

import sqlalchemy as sa
from alembic import op

revision = "material_receipt_proposal_01"
down_revision = "b9c0d1e2f3a4"  # must refresh before activation
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "material_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "purchase_item_id",
            sa.Integer(),
            sa.ForeignKey("purchase_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation_key", sa.String(160), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("source_document_ref", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id",
            "operation_key",
            name="uq_material_receipt_project_operation",
        ),
        sa.CheckConstraint(
            "qty > 0",
            name="ck_material_receipt_qty_positive",
        ),
    )
    op.create_index(
        "ix_material_receipts_project_id",
        "material_receipts",
        ["project_id"],
    )
    op.create_index(
        "ix_material_receipts_purchase_item_id",
        "material_receipts",
        ["purchase_item_id"],
    )


def downgrade():
    # Proposal ONLY: never drop data automatically.
    # project CASCADE retention unresolved; no physical-balance or
    # service guarantees. Manual history-retention review required.
    raise RuntimeError(
        "material_receipts downgrade requires manual history-retention "
        "review; refusing to drop data automatically"
    )
