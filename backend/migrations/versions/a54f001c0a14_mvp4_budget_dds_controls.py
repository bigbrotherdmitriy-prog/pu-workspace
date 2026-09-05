"""add MVP4 budget and DDS control links

Revision ID: a54f001c0a14
Revises: a54f001c0a13
"""
from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a14"
down_revision = "a54f001c0a13"
branch_labels = None
depends_on = None


def _add_budget_control_columns() -> None:
    table = "budget_lines"
    op.add_column(table, sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(table, sa.Column("schedule_item_id", sa.Integer(), sa.ForeignKey("schedule_items.id", ondelete="SET NULL")))
    op.add_column(table, sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL")))
    op.add_column(table, sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL")))
    op.add_column(table, sa.Column("source_document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT")))
    op.add_column(table, sa.Column("evidence_id", sa.String(36)))
    op.add_column(table, sa.Column("evidence_revision", sa.Integer()))
    op.add_column(table, sa.Column("evidence_assessment_version", sa.Integer()))
    op.add_column(table, sa.Column("confidence", sa.Float()))
    op.add_column(table, sa.Column("review_status", sa.String(30), nullable=False, server_default="pending_confirmation"))
    op.add_column(table, sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")))
    op.add_column(table, sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    for column in ("schedule_item_id", "task_id", "source_document_id", "source_document_version_id", "evidence_id", "review_status"):
        op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_check_constraint(f"ck_{table.removesuffix('s')}_record_version", table, "record_version > 0")
    op.create_check_constraint(f"ck_{table.removesuffix('s')}_confidence", table, "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)")
    op.create_check_constraint(
        f"ck_{table.removesuffix('s')}_review_status",
        table,
        "review_status IN ('pending_confirmation','required','confirmed','rejected')",
    )


def upgrade():
    _add_budget_control_columns()
    # cash_flow_entries already has source_document_id, schedule_item_id and
    # budget_line_id from d8b2f14c7a30. Add only the new controls explicitly.
    op.add_column("cash_flow_entries", sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("cash_flow_entries", sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL")))
    op.add_column("cash_flow_entries", sa.Column("source_document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT")))
    op.add_column("cash_flow_entries", sa.Column("evidence_id", sa.String(36)))
    op.add_column("cash_flow_entries", sa.Column("evidence_revision", sa.Integer()))
    op.add_column("cash_flow_entries", sa.Column("evidence_assessment_version", sa.Integer()))
    op.add_column("cash_flow_entries", sa.Column("confidence", sa.Float()))
    op.add_column("cash_flow_entries", sa.Column("review_status", sa.String(30), nullable=False, server_default="pending_confirmation"))
    op.add_column("cash_flow_entries", sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")))
    op.add_column("cash_flow_entries", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    for column in ("task_id", "source_document_version_id", "evidence_id", "review_status"):
        op.create_index(f"ix_cash_flow_entries_{column}", "cash_flow_entries", [column])
    op.create_check_constraint("ck_cash_flow_record_version", "cash_flow_entries", "record_version > 0")
    op.create_check_constraint("ck_cash_flow_confidence", "cash_flow_entries", "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)")
    op.create_check_constraint(
        "ck_cash_flow_review_status",
        "cash_flow_entries",
        "review_status IN ('pending_confirmation','required','confirmed','rejected')",
    )

    op.create_table(
        "cash_flow_fact_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cash_flow_entry_id", sa.Integer(), sa.ForeignKey("cash_flow_entries.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(20), nullable=False),
        sa.Column("previous_actual_amount", sa.Numeric(18, 2)),
        sa.Column("previous_actual_date", sa.Date()),
        sa.Column("resulting_actual_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("resulting_actual_date", sa.Date(), nullable=False),
        sa.Column("resulting_record_version", sa.Integer(), nullable=False),
        sa.Column("changed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cash_flow_entry_id", "sequence", name="uq_cash_flow_fact_history_sequence"),
        sa.CheckConstraint("sequence > 0 AND resulting_record_version > 0", name="ck_cash_flow_fact_history_version"),
        sa.CheckConstraint("event IN ('confirmed','corrected')", name="ck_cash_flow_fact_history_event"),
    )
    for column in ("cash_flow_entry_id", "project_id", "changed_by_user_id"):
        op.create_index(f"ix_cash_flow_fact_history_{column}", "cash_flow_fact_history", [column])


def downgrade():
    op.drop_table("cash_flow_fact_history")
    for name in ("ck_cash_flow_review_status", "ck_cash_flow_confidence", "ck_cash_flow_record_version"):
        op.drop_constraint(name, "cash_flow_entries", type_="check")
    for name in ("ck_budget_line_review_status", "ck_budget_line_confidence", "ck_budget_line_record_version"):
        op.drop_constraint(name, "budget_lines", type_="check")
    for table, columns in (
        ("cash_flow_entries", (
            "confirmed_at", "confirmed_by_user_id", "review_status", "confidence",
            "evidence_assessment_version", "evidence_revision", "evidence_id",
            "source_document_version_id", "task_id", "record_version",
        )),
        ("budget_lines", (
            "confirmed_at", "confirmed_by_user_id", "review_status", "confidence",
            "evidence_assessment_version", "evidence_revision", "evidence_id",
            "source_document_version_id", "source_document_id", "task_id",
            "schedule_item_id", "record_version",
        )),
    ):
        for column in columns:
            op.drop_column(table, column)
