"""Add synthetic provider action runtime records.

Revision ID: a54f001c0a06
Revises: a54f001c0a05
"""
from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a06"
down_revision = "a54f001c0a05"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "v54_provider_actions",
        sa.Column("action_id", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("mailbox_key", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("synthetic_only", sa.Boolean(), nullable=False),
        sa.Column("action_kind", sa.String(60), nullable=False),
        sa.Column("reversibility", sa.String(20), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("command_key", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        sa.Column("evidence_pins", sa.JSON(), nullable=False),
        sa.Column("authority_epoch", sa.Integer(), nullable=False),
        sa.Column("capability_version", sa.Integer(), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("relation_kind", sa.String(20), nullable=True),
        sa.Column("relation_action_id", sa.String(100), nullable=True),
        sa.Column("envelope_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), server_default="FROZEN", nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision > 0 AND organization_id > 0 AND project_id > 0",
            name="ck_v54_provider_action_scope",
        ),
        sa.CheckConstraint(
            "mode = 'CONFIRM' AND synthetic_only = true AND provider = 'synthetic'",
            name="ck_v54_provider_confirm_synthetic",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64 AND length(mailbox_key) = 64 "
            "AND length(envelope_hash) = 64",
            name="ck_v54_provider_action_hashes",
        ),
        sa.CheckConstraint(
            "state IN ('FROZEN','READY','EXECUTING','APPLIED','NOT_APPLIED','UNKNOWN','BLOCKED')",
            name="ck_v54_provider_action_state",
        ),
        sa.PrimaryKeyConstraint("action_id", "revision"),
        sa.UniqueConstraint(
            "organization_id", "action_id", "revision",
            name="uq_v54_provider_action_scope",
        ),
        sa.UniqueConstraint(
            "organization_id", "mailbox_key", "command_key",
            name="uq_v54_provider_command",
        ),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key",
            name="uq_v54_provider_idempotency",
        ),
    )

    op.create_table(
        "v54_provider_action_approvals",
        sa.Column("id", sa.String(100), nullable=False),
        sa.Column("action_id", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("mailbox_key", sa.String(64), nullable=False),
        sa.Column("command_key", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("envelope_hash", sa.String(64), nullable=False),
        sa.Column("authority_epoch", sa.Integer(), nullable=False),
        sa.Column("capability_version", sa.Integer(), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), server_default="GRANTED", nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('GRANTED','REVOKED','EXPIRED')",
            name="ck_v54_provider_approval_state",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64 AND length(mailbox_key) = 64 "
            "AND length(envelope_hash) = 64",
            name="ck_v54_provider_approval_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id", "revision"],
            ["v54_provider_actions.organization_id", "v54_provider_actions.action_id",
             "v54_provider_actions.revision"],
            name="fk_v54_provider_approval_action", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action_id", "revision", name="uq_v54_provider_action_approval",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", "action_id", "revision",
            name="uq_v54_provider_approval_binding",
        ),
    )

    op.create_table(
        "v54_provider_dispatch_outbox",
        sa.Column("action_id", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.String(100), nullable=False),
        sa.Column("envelope_hash", sa.String(64), nullable=False),
        sa.Column("pending", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id", "revision"],
            ["v54_provider_actions.organization_id", "v54_provider_actions.action_id",
             "v54_provider_actions.revision"],
            name="fk_v54_provider_outbox_action", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id", "organization_id", "action_id", "revision"],
            ["v54_provider_action_approvals.id", "v54_provider_action_approvals.organization_id",
             "v54_provider_action_approvals.action_id", "v54_provider_action_approvals.revision"],
            name="fk_v54_provider_outbox_approval", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["background_jobs.id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("action_id", "revision"),
    )
    op.create_index(
        "ix_v54_provider_outbox_pending", "v54_provider_dispatch_outbox",
        ["pending", "organization_id"],
    )

    op.create_table(
        "v54_provider_execution_attempts",
        sa.Column("action_id", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(100), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("first_job_id", sa.Integer(), nullable=False),
        sa.Column("adapter_name", sa.String(40), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('DISPATCHING','APPLIED','NOT_APPLIED','UNKNOWN')",
            name="ck_v54_provider_attempt_state",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id", "revision"],
            ["v54_provider_actions.organization_id", "v54_provider_actions.action_id",
             "v54_provider_actions.revision"],
            name="fk_v54_provider_attempt_action", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["first_job_id"], ["background_jobs.id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("action_id", "revision"),
        sa.UniqueConstraint("attempt_id", name="uq_v54_provider_attempt_id"),
    )

    op.create_table(
        "v54_provider_outcome_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action_id", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(100), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("mailbox_key", sa.String(64), nullable=False),
        sa.Column("command_key", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("envelope_hash", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("retry_safe", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("late", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("external_ref", sa.String(200), nullable=True),
        sa.Column("safe_code", sa.String(60), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('APPLIED','NOT_APPLIED','UNKNOWN')",
            name="ck_v54_provider_observation_outcome",
        ),
        sa.CheckConstraint(
            "source IN ('DISPATCH','RECONCILE','LATE_RECEIPT','PROCESS_RECOVERY')",
            name="ck_v54_provider_observation_source",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id", "revision"],
            ["v54_provider_actions.organization_id", "v54_provider_actions.action_id",
             "v54_provider_actions.revision"],
            name="fk_v54_provider_observation_action", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["background_jobs.id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action_id", "revision", "sequence",
            name="uq_v54_provider_observation_sequence",
        ),
    )


def downgrade():
    op.drop_table("v54_provider_outcome_observations")
    op.drop_table("v54_provider_execution_attempts")
    op.drop_index(
        "ix_v54_provider_outbox_pending",
        table_name="v54_provider_dispatch_outbox",
    )
    op.drop_table("v54_provider_dispatch_outbox")
    op.drop_table("v54_provider_action_approvals")
    op.drop_table("v54_provider_actions")
