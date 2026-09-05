"""harden MVP4 finance evidence and payment events

Revision ID: e75c4a1d9f20
Revises: e74a1c5d09b2
"""
from alembic import op
import sqlalchemy as sa


revision = "e75c4a1d9f20"
down_revision = "e74a1c5d09b2"
branch_labels = None
depends_on = None


_CURRENCIES = (
    "AED", "AMD", "AUD", "AZN", "BGN", "BRL", "BYN", "CAD", "CHF", "CNY",
    "CZK", "DKK", "EUR", "GBP", "GEL", "HKD", "HUF", "INR", "JPY", "KGS",
    "KRW", "KZT", "MDL", "NOK", "PLN", "RON", "RSD", "RUB", "SEK", "SGD",
    "THB", "TJS", "TRY", "UAH", "USD", "UZS", "VND", "ZAR",
)


def _currency_check(column: str) -> str:
    quoted = ",".join(f"'{value}'" for value in _CURRENCIES)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_check_constraint("ck_budget_lines_currency", "budget_lines", _currency_check("currency"))
    op.add_column("cash_flow_entries", sa.Column("task_id", sa.Integer(), nullable=True))
    op.add_column("cash_flow_entries", sa.Column("source_document_version_id", sa.Integer(), nullable=True))
    op.add_column("cash_flow_entries", sa.Column("source_document_sha256", sa.String(64), nullable=True))
    op.add_column("cash_flow_entries", sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"))
    op.create_foreign_key("fk_cash_flow_task", "cash_flow_entries", "tasks", ["task_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_cash_flow_document_version", "cash_flow_entries", "document_versions", ["source_document_version_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_cash_flow_entries_task_id", "cash_flow_entries", ["task_id"])
    op.create_index("ix_cash_flow_entries_source_document_version_id", "cash_flow_entries", ["source_document_version_id"])
    op.create_check_constraint("ck_cash_flow_currency", "cash_flow_entries", _currency_check("currency"))

    for table in ("procurement_items", "acceptance_acts"):
        op.add_column(table, sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"))
        op.create_check_constraint(f"ck_{table}_currency", table, _currency_check("currency"))

    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cash_flow_entry_id", sa.Integer(), sa.ForeignKey("cash_flow_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("supersedes_event_id", sa.Integer(), sa.ForeignKey("payment_events.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("source_document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("source_document_sha256", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("event_type IN ('confirmation','correction','reversal')", name="ck_payment_event_type"),
        sa.CheckConstraint(_currency_check("currency"), name="ck_payment_event_currency"),
        sa.CheckConstraint("(event_type = 'reversal' AND amount IS NULL AND payment_date IS NULL) OR (event_type <> 'reversal' AND amount > 0 AND payment_date IS NOT NULL)", name="ck_payment_event_payload"),
        sa.UniqueConstraint("cash_flow_entry_id", "idempotency_key", name="uq_payment_event_idempotency"),
    )
    for column in ("project_id", "cash_flow_entry_id", "event_type", "supersedes_event_id", "source_document_version_id"):
        op.create_index(f"ix_payment_events_{column}", "payment_events", [column])


def downgrade() -> None:
    op.drop_table("payment_events")
    for table in ("acceptance_acts", "procurement_items"):
        op.drop_constraint(f"ck_{table}_currency", table, type_="check")
        op.drop_column(table, "currency")
    op.drop_constraint("ck_cash_flow_currency", "cash_flow_entries", type_="check")
    op.drop_index("ix_cash_flow_entries_source_document_version_id", table_name="cash_flow_entries")
    op.drop_index("ix_cash_flow_entries_task_id", table_name="cash_flow_entries")
    op.drop_constraint("fk_cash_flow_document_version", "cash_flow_entries", type_="foreignkey")
    op.drop_constraint("fk_cash_flow_task", "cash_flow_entries", type_="foreignkey")
    for column in ("currency", "source_document_sha256", "source_document_version_id", "task_id"):
        op.drop_column("cash_flow_entries", column)
    op.drop_constraint("ck_budget_lines_currency", "budget_lines", type_="check")
