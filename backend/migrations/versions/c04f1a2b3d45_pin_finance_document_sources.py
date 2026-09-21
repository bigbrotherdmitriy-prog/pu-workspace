"""Pin source versions for document-derived finance records.

Revision ID: c04f1a2b3d45
Revises: b89e7f9a5a67
"""

from alembic import op
import sqlalchemy as sa


revision = "c04f1a2b3d45"
down_revision = "b89e7f9a5a67"
branch_labels = None
depends_on = None


_PIN_PAIR = (
    "(source_document_version_id IS NULL AND source_document_sha256 IS NULL) OR "
    "(source_document_version_id IS NOT NULL AND source_document_sha256 IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column("budget_lines", sa.Column("source_document_id", sa.Integer(), nullable=True))
    op.add_column("budget_lines", sa.Column("source_document_version_id", sa.Integer(), nullable=True))
    op.add_column("budget_lines", sa.Column("source_document_sha256", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_budget_lines_source_document", "budget_lines", "documents",
        ["source_document_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_budget_lines_source_document_version", "budget_lines", "document_versions",
        ["source_document_version_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_budget_lines_source_document_id", "budget_lines", ["source_document_id"])
    op.create_index(
        "ix_budget_lines_source_document_version_id",
        "budget_lines",
        ["source_document_version_id"],
    )
    op.create_check_constraint("ck_budget_line_source_pin_pair", "budget_lines", _PIN_PAIR)

    op.add_column("acceptance_acts", sa.Column("source_document_version_id", sa.Integer(), nullable=True))
    op.add_column("acceptance_acts", sa.Column("source_document_sha256", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_acceptance_acts_source_document_version", "acceptance_acts", "document_versions",
        ["source_document_version_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index(
        "ix_acceptance_acts_source_document_version_id",
        "acceptance_acts",
        ["source_document_version_id"],
    )
    op.create_check_constraint("ck_acceptance_act_source_pin_pair", "acceptance_acts", _PIN_PAIR)


def downgrade() -> None:
    op.drop_constraint("ck_acceptance_act_source_pin_pair", "acceptance_acts", type_="check")
    op.drop_index("ix_acceptance_acts_source_document_version_id", table_name="acceptance_acts")
    op.drop_constraint(
        "fk_acceptance_acts_source_document_version", "acceptance_acts", type_="foreignkey",
    )
    op.drop_column("acceptance_acts", "source_document_sha256")
    op.drop_column("acceptance_acts", "source_document_version_id")

    op.drop_constraint("ck_budget_line_source_pin_pair", "budget_lines", type_="check")
    op.drop_index("ix_budget_lines_source_document_version_id", table_name="budget_lines")
    op.drop_index("ix_budget_lines_source_document_id", table_name="budget_lines")
    op.drop_constraint("fk_budget_lines_source_document_version", "budget_lines", type_="foreignkey")
    op.drop_constraint("fk_budget_lines_source_document", "budget_lines", type_="foreignkey")
    op.drop_column("budget_lines", "source_document_sha256")
    op.drop_column("budget_lines", "source_document_version_id")
    op.drop_column("budget_lines", "source_document_id")
