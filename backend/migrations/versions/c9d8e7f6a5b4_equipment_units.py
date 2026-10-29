"""equipment units register

Revision ID: c9d8e7f6a5b4
Revises: f3a4b5c6d7e8
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c9d8e7f6a5b4"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipment_units",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rd_spec_item_id", sa.Integer(), sa.ForeignKey("rd_spec_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("purchase_item_id", sa.Integer(), sa.ForeignKey("purchase_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("unit_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type_mark", sa.String(length=500), nullable=True),
        sa.Column("manufacturer", sa.String(length=300), nullable=True),
        sa.Column("serial_number", sa.String(length=200), nullable=True),
        sa.Column("location", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
        sa.Column("received_date", sa.Date(), nullable=True),
        sa.Column("installed_date", sa.Date(), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.Column("service_interval_months", sa.Integer(), nullable=True),
        sa.Column("last_service_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "rd_spec_item_id", "unit_no", name="uq_equipment_unit"),
    )
    op.create_index("ix_equipment_units_project_id", "equipment_units", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_equipment_units_project_id", table_name="equipment_units")
    op.drop_table("equipment_units")
