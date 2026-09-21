"""Link acceptance acts to budget lines for actual-work projection.

Revision ID: d15a7c9e2b40
Revises: c04f1a2b3d45
"""

from alembic import op
import sqlalchemy as sa


revision = "d15a7c9e2b40"
down_revision = "c04f1a2b3d45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("acceptance_acts", sa.Column("budget_line_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_acceptance_acts_budget_line", "acceptance_acts", "budget_lines",
        ["budget_line_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_acceptance_acts_budget_line_id", "acceptance_acts", ["budget_line_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_acceptance_acts_budget_line_id", table_name="acceptance_acts")
    op.drop_constraint(
        "fk_acceptance_acts_budget_line", "acceptance_acts", type_="foreignkey",
    )
    op.drop_column("acceptance_acts", "budget_line_id")
