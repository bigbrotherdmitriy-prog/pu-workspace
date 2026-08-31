"""add organization requisites registry

Revision ID: d20f6a7b8c91
Revises: c8f3a4b5d6e7
"""
from alembic import op
import sqlalchemy as sa

revision = "d20f6a7b8c91"
down_revision = "c8f3a4b5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    fields = (
        ("legal_name", sa.String(500)), ("inn", sa.String(12)), ("kpp", sa.String(9)),
        ("ogrn", sa.String(15)), ("okpo", sa.String(14)), ("okato", sa.String(20)),
        ("oktmo", sa.String(20)), ("okogu", sa.String(20)), ("okved", sa.String(500)),
        ("legal_address", sa.Text()), ("postal_address", sa.Text()),
        ("phone", sa.String(100)), ("email", sa.String(255)),
        ("director_name", sa.String(255)), ("chief_accountant", sa.String(255)),
        ("registration_details", sa.Text()), ("tax_office", sa.String(500)),
        ("bank_name", sa.String(500)), ("bank_address", sa.Text()),
        ("settlement_account", sa.String(30)), ("correspondent_account", sa.String(30)),
        ("bik", sa.String(9)),
    )
    for name, type_ in fields:
        op.add_column("organizations", sa.Column(name, type_, nullable=True))
    op.add_column("organizations", sa.Column("requisites_status", sa.String(30), nullable=False, server_default="draft"))
    op.add_column("organizations", sa.Column("source_document_id", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_organizations_inn", "organizations", ["inn"])
    op.create_index("ix_organizations_source_document_id", "organizations", ["source_document_id"])
    op.create_foreign_key("fk_organizations_source_document_id", "organizations", "documents", ["source_document_id"], ["id"], ondelete="SET NULL")
    op.add_column("contracts", sa.Column("counterparty_organization_id", sa.Integer(), nullable=True))
    op.create_index("ix_contracts_counterparty_organization_id", "contracts", ["counterparty_organization_id"])
    op.create_foreign_key("fk_contracts_counterparty_organization_id", "contracts", "organizations", ["counterparty_organization_id"], ["id"], ondelete="SET NULL")


def downgrade():
    op.drop_constraint("fk_contracts_counterparty_organization_id", "contracts", type_="foreignkey")
    op.drop_index("ix_contracts_counterparty_organization_id", table_name="contracts")
    op.drop_column("contracts", "counterparty_organization_id")
    op.drop_constraint("fk_organizations_source_document_id", "organizations", type_="foreignkey")
    op.drop_index("ix_organizations_source_document_id", table_name="organizations")
    op.drop_index("ix_organizations_inn", table_name="organizations")
    for name in (
        "updated_at", "source_document_id", "requisites_status", "bik", "correspondent_account",
        "settlement_account", "bank_address", "bank_name", "tax_office", "registration_details",
        "chief_accountant", "director_name", "email", "phone", "postal_address", "legal_address",
        "okved", "okogu", "oktmo", "okato", "okpo", "ogrn", "kpp", "inn", "legal_name",
    ):
        op.drop_column("organizations", name)
