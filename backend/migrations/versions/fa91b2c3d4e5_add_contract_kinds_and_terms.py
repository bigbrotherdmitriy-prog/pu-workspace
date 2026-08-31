"""add contract kinds and commercial terms

Revision ID: fa91b2c3d4e5
Revises: f47a50d44d91
"""
from alembic import op
import sqlalchemy as sa

revision = "fa91b2c3d4e5"
down_revision = "f47a50d44d91"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contracts", sa.Column("contract_kind", sa.String(length=30), nullable=False, server_default="customer"))
    op.add_column("contracts", sa.Column("parent_contract_id", sa.Integer(), nullable=True))
    op.add_column("contracts", sa.Column("amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("contracts", sa.Column("advance_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("contracts", sa.Column("retention_percent", sa.Numeric(5, 2), nullable=True))
    op.add_column("contracts", sa.Column("warranty_until", sa.Date(), nullable=True))
    op.create_foreign_key("fk_contracts_parent_contract_id", "contracts", "contracts", ["parent_contract_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_contracts_contract_kind", "contracts", ["contract_kind"])
    op.create_index("ix_contracts_parent_contract_id", "contracts", ["parent_contract_id"])


def downgrade():
    op.drop_index("ix_contracts_parent_contract_id", table_name="contracts")
    op.drop_index("ix_contracts_contract_kind", table_name="contracts")
    op.drop_constraint("fk_contracts_parent_contract_id", "contracts", type_="foreignkey")
    for name in ("warranty_until", "retention_percent", "advance_amount", "amount", "parent_contract_id", "contract_kind"):
        op.drop_column("contracts", name)
