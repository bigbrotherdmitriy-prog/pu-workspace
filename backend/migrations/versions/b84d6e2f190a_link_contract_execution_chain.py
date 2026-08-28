"""link contracts to schedule baselines

Revision ID: b84d6e2f190a
Revises: a61b2c3d4e5f
"""
from alembic import op
import sqlalchemy as sa

revision = "b84d6e2f190a"
down_revision = "a61b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("schedule_baselines", sa.Column("contract_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_schedule_baselines_contract_id", "schedule_baselines", "contracts", ["contract_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_schedule_baselines_contract_id", "schedule_baselines", ["contract_id"])


def downgrade():
    op.drop_index("ix_schedule_baselines_contract_id", table_name="schedule_baselines")
    op.drop_constraint("fk_schedule_baselines_contract_id", "schedule_baselines", type_="foreignkey")
    op.drop_column("schedule_baselines", "contract_id")
