"""add schedule planner fields

Revision ID: f18b7c42d9a1
Revises: c24d9e7a61b0
"""
from alembic import op
import sqlalchemy as sa

revision = "f18b7c42d9a1"
down_revision = "c24d9e7a61b0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("schedule_items", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("schedule_items", sa.Column("parent_id", sa.Integer(), nullable=True))
    op.add_column("schedule_items", sa.Column("duration_days", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("schedule_items", sa.Column("is_milestone", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("schedule_items", sa.Column("predecessor_ids", sa.String(2000), nullable=True))
    op.add_column("schedule_items", sa.Column("constraint_type", sa.String(30), nullable=True))
    op.add_column("schedule_items", sa.Column("constraint_date", sa.Date(), nullable=True))
    op.create_foreign_key("fk_schedule_items_parent", "schedule_items", "schedule_items", ["parent_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_schedule_items_sort_order", "schedule_items", ["sort_order"])
    op.create_index("ix_schedule_items_parent_id", "schedule_items", ["parent_id"])


def downgrade():
    op.drop_index("ix_schedule_items_parent_id", table_name="schedule_items")
    op.drop_index("ix_schedule_items_sort_order", table_name="schedule_items")
    op.drop_constraint("fk_schedule_items_parent", "schedule_items", type_="foreignkey")
    for column in ("constraint_date", "constraint_type", "predecessor_ids", "is_milestone", "duration_days", "parent_id", "sort_order"):
        op.drop_column("schedule_items", column)
