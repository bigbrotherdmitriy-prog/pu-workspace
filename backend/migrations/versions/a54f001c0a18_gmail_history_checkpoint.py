"""Add durable mailbox-scoped Gmail history checkpoint.

Revision ID: a54f001c0a18
Revises: a54f001c0a17
"""

from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a18"
down_revision = "a54f001c0a17"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "v54_gmail_history_checkpoints",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("identity_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("mail_connection_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("binding_epoch", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("last_history_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="resync_required"),
        sa.Column("sync_mode", sa.String(16), nullable=True),
        sa.Column("active_job_id", sa.Integer(), sa.ForeignKey("background_jobs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("last_job_id", sa.Integer(), nullable=True),
        sa.Column("last_result", sa.JSON(), nullable=True),
        sa.Column("checkpoint_epoch", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "id", name="uq_v54_gmail_history_checkpoint_scope"),
        sa.UniqueConstraint("organization_id", "mail_connection_id", name="uq_v54_gmail_history_checkpoint_mailbox"),
        sa.ForeignKeyConstraint(
            ["organization_id", "identity_id"],
            ["v54_connection_identities.organization_id", "v54_connection_identities.id"],
            ondelete="RESTRICT", name="fk_v54_gmail_history_checkpoint_identity",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "mail_connection_id"],
            ["v54_mail_connections.organization_id", "v54_mail_connections.id"],
            ondelete="RESTRICT", name="fk_v54_gmail_history_checkpoint_mail",
        ),
        sa.CheckConstraint(
            "credential_generation > 0 AND binding_epoch > 0 AND checkpoint_epoch > 0",
            name="ck_v54_gmail_history_checkpoint_epochs",
        ),
        sa.CheckConstraint(
            "status IN ('active','syncing','resync_required','blocked')",
            name="ck_v54_gmail_history_checkpoint_status",
        ),
        sa.CheckConstraint(
            "sync_mode IS NULL OR sync_mode IN ('incremental','resync')",
            name="ck_v54_gmail_history_checkpoint_mode",
        ),
        sa.CheckConstraint(
            "(status = 'syncing' AND active_job_id IS NOT NULL AND sync_mode IS NOT NULL) OR "
            "(status != 'syncing' AND active_job_id IS NULL AND sync_mode IS NULL)",
            name="ck_v54_gmail_history_checkpoint_claim",
        ),
    )
    op.create_index(
        "ix_v54_gmail_history_checkpoint_project",
        "v54_gmail_history_checkpoints", ["project_id"],
    )
    op.create_index(
        "ix_v54_gmail_history_checkpoint_job",
        "v54_gmail_history_checkpoints", ["active_job_id"],
    )
    op.create_table(
        "v54_gmail_history_checkpoint_events",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("from_epoch", sa.Integer(), nullable=False),
        sa.Column("to_epoch", sa.Integer(), nullable=False),
        sa.Column("outcome_code", sa.String(32), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id", "checkpoint_id"],
            ["v54_gmail_history_checkpoints.organization_id", "v54_gmail_history_checkpoints.id"],
            ondelete="RESTRICT", name="fk_v54_gmail_history_event_checkpoint",
        ),
        sa.CheckConstraint(
            "from_epoch > 0 AND to_epoch >= from_epoch",
            name="ck_v54_gmail_history_event_epochs",
        ),
        sa.CheckConstraint(
            "outcome_code IN ('sync_claimed','sync_completed','cursor_expired',"
            "'resync_completed','sync_failed','generation_rejected','generation_rotated')",
            name="ck_v54_gmail_history_event_outcome",
        ),
    )
    op.create_index(
        "ix_v54_gmail_history_event_checkpoint",
        "v54_gmail_history_checkpoint_events", ["checkpoint_id", "created_at"],
    )
    op.create_index(
        "ix_v54_gmail_history_event_job",
        "v54_gmail_history_checkpoint_events", ["job_id"],
    )


def downgrade():
    op.drop_table("v54_gmail_history_checkpoint_events")
    op.drop_table("v54_gmail_history_checkpoints")
