"""Add project-scoped governance lifecycle relations.

Revision ID: b87e7f9a3f45
Revises: b86e7f9a2e34
"""

from alembic import op
import sqlalchemy as sa


revision = "b87e7f9a3f45"
down_revision = "b86e7f9a2e34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("risks", sa.Column("obligation_id", sa.Integer(), nullable=True))
    op.add_column("risks", sa.Column("task_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_risks_obligation", "risks", "obligations", ["obligation_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_risks_task", "risks", "tasks", ["task_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_risks_obligation_id", "risks", ["obligation_id"])
    op.create_index("ix_risks_task_id", "risks", ["task_id"])

    op.add_column("decisions", sa.Column("obligation_id", sa.Integer(), nullable=True))
    op.add_column("decisions", sa.Column("task_id", sa.Integer(), nullable=True))
    op.add_column("decisions", sa.Column("risk_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_decisions_obligation", "decisions", "obligations", ["obligation_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_decisions_task", "decisions", "tasks", ["task_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_decisions_risk", "decisions", "risks", ["risk_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_decisions_obligation_id", "decisions", ["obligation_id"])
    op.create_index("ix_decisions_task_id", "decisions", ["task_id"])
    op.create_index("ix_decisions_risk_id", "decisions", ["risk_id"])


def downgrade() -> None:
    op.drop_index("ix_decisions_risk_id", table_name="decisions")
    op.drop_index("ix_decisions_task_id", table_name="decisions")
    op.drop_index("ix_decisions_obligation_id", table_name="decisions")
    op.drop_constraint("fk_decisions_risk", "decisions", type_="foreignkey")
    op.drop_constraint("fk_decisions_task", "decisions", type_="foreignkey")
    op.drop_constraint("fk_decisions_obligation", "decisions", type_="foreignkey")
    op.drop_column("decisions", "risk_id")
    op.drop_column("decisions", "task_id")
    op.drop_column("decisions", "obligation_id")

    op.drop_index("ix_risks_task_id", table_name="risks")
    op.drop_index("ix_risks_obligation_id", table_name="risks")
    op.drop_constraint("fk_risks_task", "risks", type_="foreignkey")
    op.drop_constraint("fk_risks_obligation", "risks", type_="foreignkey")
    op.drop_column("risks", "task_id")
    op.drop_column("risks", "obligation_id")
