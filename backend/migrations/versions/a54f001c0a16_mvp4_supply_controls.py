"""Add evidence-backed supply workflow tables.

Revision ID: a54f001c0a16
Revises: a54f001c0a15
"""
from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a16"
down_revision = "a54f001c0a15"
branch_labels = None
depends_on = None


_SUPPLY_STATES = (
    "'needs_review','request_pending_approval','request_rejected','request_approved',"
    "'order_draft','order_approved','order_recorded','partially_delivered','delivered',"
    "'delivery_discrepancy','act_pending_approval','partially_accepted','accepted','cancelled'"
)


def upgrade():
    op.create_table(
        "mvp4_supply_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schedule_baseline_id", sa.Integer(), sa.ForeignKey("schedule_baselines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schedule_baseline_version", sa.Integer(), nullable=False),
        sa.Column("schedule_item_id", sa.Integer(), sa.ForeignKey("schedule_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evidence_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("evidence_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("source_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("request_key", sa.String(120), nullable=False),
        sa.Column("request_payload_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("supplier", sa.String(500), nullable=False),
        sa.Column("requested_quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("review_state", sa.String(30), nullable=False),
        sa.Column("evidence_confidence", sa.Float()),
        sa.Column("ordered_quantity", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("delivered_quantity", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("accepted_quantity", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("pending_acceptance_quantity", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("order_reference", sa.String(200)),
        sa.Column("act_number", sa.String(200)),
        sa.Column("discrepancy_code", sa.String(60)),
        sa.Column("discrepancy_note", sa.String(1000)),
        sa.Column("external_action_status", sa.String(30), nullable=False, server_default="not_created"),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("request_approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("order_approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("act_approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "project_id", "request_key", name="uq_mvp4_supply_request_key"),
        sa.ForeignKeyConstraint(
            ["organization_id", "evidence_id", "source_id", "source_version_id"],
            ["v54_evidence.organization_id", "v54_evidence.id", "v54_evidence.source_id", "v54_evidence.source_version_id"],
            name="fk_mvp4_supply_exact_evidence",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("record_version > 0", name="ck_mvp4_supply_record_version"),
        sa.CheckConstraint("evidence_revision = 1", name="ck_mvp4_supply_evidence_revision"),
        sa.CheckConstraint("requested_quantity > 0", name="ck_mvp4_supply_requested_quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_mvp4_supply_unit_price"),
        sa.CheckConstraint("ordered_quantity >= 0", name="ck_mvp4_supply_ordered_quantity"),
        sa.CheckConstraint("delivered_quantity >= 0", name="ck_mvp4_supply_delivered_quantity"),
        sa.CheckConstraint("accepted_quantity >= 0", name="ck_mvp4_supply_accepted_quantity"),
        sa.CheckConstraint("pending_acceptance_quantity >= 0", name="ck_mvp4_supply_pending_acceptance_quantity"),
        sa.CheckConstraint(f"status IN ({_SUPPLY_STATES})", name="ck_mvp4_supply_status"),
        sa.CheckConstraint("review_state IN ('needs_review','verified','rejected')", name="ck_mvp4_supply_review_state"),
        sa.CheckConstraint("external_action_status = 'not_created'", name="ck_mvp4_supply_no_external_action"),
    )
    for column in (
        "organization_id", "project_id", "contract_id", "schedule_baseline_id", "schedule_item_id", "task_id",
        "document_version_id", "evidence_id", "source_version_id", "status", "review_state",
    ):
        op.create_index(f"ix_mvp4_supply_cases_{column}", "mvp4_supply_cases", [column])

    op.create_table(
        "mvp4_supply_case_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supply_case_id", sa.Integer(), sa.ForeignKey("mvp4_supply_cases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("resulting_record_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("evidence_pin", sa.JSON()),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("supply_case_id", "sequence", name="uq_mvp4_supply_version_sequence"),
        sa.CheckConstraint("sequence > 0 AND resulting_record_version > 0", name="ck_mvp4_supply_version_positive"),
    )
    op.create_table(
        "mvp4_supply_command_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supply_case_id", sa.Integer(), sa.ForeignKey("mvp4_supply_cases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("command_key", sa.String(120), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("result_status", sa.String(40), nullable=False),
        sa.Column("resulting_record_version", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("supply_case_id", "command_key", name="uq_mvp4_supply_command_key"),
        sa.CheckConstraint("resulting_record_version > 0", name="ck_mvp4_supply_receipt_version"),
    )
    for table in ("mvp4_supply_case_versions", "mvp4_supply_command_receipts"):
        for column in ("supply_case_id", "organization_id", "project_id", "actor_user_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    op.drop_table("mvp4_supply_command_receipts")
    op.drop_table("mvp4_supply_case_versions")
    op.drop_table("mvp4_supply_cases")
