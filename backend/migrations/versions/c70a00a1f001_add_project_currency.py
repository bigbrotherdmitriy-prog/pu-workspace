"""Add the single-currency invariant for projects.

Revision ID: c70a00a1f001
Revises: b62f9d3a4c10
"""

from alembic import op
import sqlalchemy as sa


revision = "c70a00a1f001"
down_revision = "b62f9d3a4c10"
branch_labels = None
depends_on = None


_CURRENCY_TABLES = (
    "budget_lines",
    "cash_flow_entries",
    "procurement_items",
    "acceptance_acts",
    "payment_events",
    "invoice_extraction_proposals",
    "contract_budget_proposals",
)


def _assert_existing_finance_is_rub() -> None:
    """Fail closed instead of silently relabelling existing financial rows."""
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    mismatches = []
    for table in _CURRENCY_TABLES:
        count = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE currency IS NULL OR currency <> 'RUB'"
        )).scalar_one()
        if count:
            mismatches.append(f"{table}={count}")
    if mismatches:
        raise RuntimeError(
            "V6-00a currency migration stopped: existing non-RUB finance rows: "
            + ", ".join(mismatches)
        )


def upgrade():
    _assert_existing_finance_is_rub()
    op.add_column(
        "projects",
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
    )
    op.create_check_constraint(
        "ck_projects_currency_format",
        "projects",
        "length(currency) = 3 AND currency = upper(currency)",
    )


def downgrade():
    op.drop_constraint("ck_projects_currency_format", "projects", type_="check")
    op.drop_column("projects", "currency")
