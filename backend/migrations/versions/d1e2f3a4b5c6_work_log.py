"""work log entries

Revision ID: d1e2f3a4b5c6
Revises: c9d8e7f6a5b4
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c9d8e7f6a5b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_log_crews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("foreman", sa.String(length=300), nullable=True),
        sa.Column("members", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_work_log_crews_project_id", "work_log_crews", ["project_id"])
    op.create_table(
        "work_log_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("object_name", sa.String(length=300), nullable=True),
        sa.Column("work_name", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=True),
        sa.Column("location", sa.String(length=300), nullable=True),
        sa.Column("weather", sa.String(length=200), nullable=True),
        sa.Column("workers", sa.Integer(), nullable=True),
        sa.Column("hours", sa.Numeric(5, 2), nullable=True),
        sa.Column("crew_id", sa.Integer(), sa.ForeignKey("work_log_crews.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("rd_spec_item_id", sa.Integer(), sa.ForeignKey("rd_spec_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("schedule_item_id", sa.Integer(), sa.ForeignKey("schedule_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_work_log_entries_project_id", "work_log_entries", ["project_id"])
    op.create_index("ix_work_log_entries_work_date", "work_log_entries", ["work_date"])


def downgrade() -> None:
    op.drop_index("ix_work_log_entries_work_date", table_name="work_log_entries")
    op.drop_index("ix_work_log_entries_project_id", table_name="work_log_entries")
    op.drop_table("work_log_entries")
    op.drop_index("ix_work_log_crews_project_id", table_name="work_log_crews")
    op.drop_table("work_log_crews")
