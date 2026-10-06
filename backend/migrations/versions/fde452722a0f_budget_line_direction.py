"""Add explicit direction to BudgetLine (ADR-V6-05-INCOME-BUDGET-RU)."""

from alembic import op
import sqlalchemy as sa


revision = "fde452722a0f"
down_revision = "d021a6c0b003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("budget_lines", sa.Column("direction", sa.String(10), nullable=True))
    op.create_check_constraint(
        "ck_budget_line_direction_valid", "budget_lines",
        "direction IS NULL OR direction IN ('inflow','outflow')",
    )
    # analytical_expense rows are always outflow by construction (the matrix
    # import they come from only ever reads direction='outflow' source rows).
    # This only restates that pre-existing fact, it does not classify anything new.
    op.execute("UPDATE budget_lines SET direction = 'outflow' WHERE line_kind = 'analytical_expense'")
    # contract_control is the one generic per-contract total line created for
    # every financial contract kind, including customer/revenue_subcontract --
    # so unlike analytical_expense it is NOT uniformly outflow. Derive it per
    # row from the linked contract's existing role (same mapping as
    # app.core.contract_roles.cash_flow_direction) via a portable correlated
    # subquery that works on both PostgreSQL and SQLite.
    op.execute(
        "UPDATE budget_lines SET direction = ("
        "  SELECT CASE"
        "    WHEN c.contract_kind IN ('customer','revenue_subcontract') THEN 'inflow'"
        "    WHEN c.contract_kind IN ('downstream_subcontract','supply') THEN 'outflow'"
        "    ELSE NULL"
        "  END FROM contracts c WHERE c.id = budget_lines.contract_id"
        ") WHERE line_kind = 'contract_control'"
    )
    # legacy_unclassified rows stay direction=NULL until manually reviewed
    # (ADR decision 4), matching the existing BUDGET_SCOPE_UNKNOWN behaviour.


def downgrade():
    # direction='inflow' and line_kind='analytical_income' cannot exist without
    # this column; losing them silently would delete real income-budget data
    # entered after this migration, not just a derived restatement.
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM budget_lines WHERE direction = 'inflow' "
        "OR line_kind = 'analytical_income')"
    )):
        raise RuntimeError("BUDGET_LINE_DIRECTION_DOWNGRADE_BLOCKED: preserve income budget data")
    op.drop_constraint("ck_budget_line_direction_valid", "budget_lines", type_="check")
    op.drop_column("budget_lines", "direction")
