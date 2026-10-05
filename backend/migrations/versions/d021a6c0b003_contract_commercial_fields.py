"""Add explicit VAT, performance dates and nullable financial provenance only."""

from alembic import op
import sqlalchemy as sa


revision = "d021a6c0b003"
down_revision = "d021a6c0b002"
branch_labels = None
depends_on = None

_SNAPSHOT_TABLES = (
    "budget_lines", "cash_flow_entries", "acceptance_acts", "payment_events",
    "contract_budget_proposals", "invoice_extraction_proposals",
)


def upgrade():
    op.add_column("contracts", sa.Column("vat_mode", sa.String(20), nullable=False, server_default="unspecified"))
    op.add_column("contracts", sa.Column("vat_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("contracts", sa.Column("performed_from", sa.Date(), nullable=True))
    op.add_column("contracts", sa.Column("performed_to", sa.Date(), nullable=True))
    op.create_check_constraint(
        "ck_contract_vat_state", "contracts",
        "(vat_mode IN ('unspecified','none') AND vat_rate IS NULL) OR "
        "(vat_mode = 'rate' AND vat_rate IS NOT NULL AND vat_rate >= 0 AND vat_rate <= 100)",
    )
    op.create_check_constraint(
        "ck_contract_performed_order", "contracts",
        "performed_from IS NULL OR performed_to IS NULL OR performed_from <= performed_to",
    )
    for table in _SNAPSHOT_TABLES:
        op.add_column(table, sa.Column("vat_snapshot", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    # Defaults added by upgrade do not count as entered business data. Any
    # explicit VAT choice, date or JSON value (including JSON null) must survive.
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM contracts WHERE vat_mode <> 'unspecified' "
        "OR vat_rate IS NOT NULL OR performed_from IS NOT NULL OR performed_to IS NOT NULL)"
    )):
        raise RuntimeError("CONTRACT_COMMERCIAL_DOWNGRADE_BLOCKED: preserve commercial fields")
    for table in _SNAPSHOT_TABLES:
        if bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE vat_snapshot IS NOT NULL)")):
            raise RuntimeError("CONTRACT_COMMERCIAL_DOWNGRADE_BLOCKED: preserve VAT provenance")
    for table in reversed(_SNAPSHOT_TABLES):
        op.drop_column(table, "vat_snapshot")
    op.drop_constraint("ck_contract_performed_order", "contracts", type_="check")
    op.drop_constraint("ck_contract_vat_state", "contracts", type_="check")
    for field in ("performed_to", "performed_from", "vat_rate", "vat_mode"):
        op.drop_column("contracts", field)
