"""Expand durable mailbox identity without backfill or activation.

Revision ID: a54f001c0a03
Revises: a54f001c0a02
"""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a03"
down_revision = "a54f001c0a02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("origin_version", sa.Integer(), server_default="1", nullable=False))
    op.create_check_constraint("ck_v54_message_origin_version", "messages", "origin_version > 0")
    op.create_table("v54_mailbox_credential_generations",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False), sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("connection_identity_id", sa.Uuid(as_uuid=False), nullable=False), sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("binding_epoch", sa.Integer(), nullable=False), sa.Column("google_token_id", sa.Integer(), nullable=True),
        sa.Column("integration_credential_id", sa.Integer(), nullable=True), sa.Column("state", sa.String(16), server_default="active", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation > 0 AND binding_epoch > 0", name="ck_v54_mailbox_credential_positive"),
        sa.CheckConstraint("state IN ('active','expired','revoked')", name="ck_v54_mailbox_credential_state"),
        sa.CheckConstraint("(google_token_id IS NOT NULL) != (integration_credential_id IS NOT NULL)", name="ck_v54_mailbox_credential_owner"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","connection_identity_id"], ["v54_connection_identities.organization_id","v54_connection_identities.id"], name="fk_v54_mailbox_credential_identity", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["google_token_id"], ["google_oauth_tokens.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["integration_credential_id"], ["integration_credentials.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("organization_id","id",name="uq_v54_mailbox_credential_scope"),
        sa.UniqueConstraint("organization_id","connection_identity_id","generation",name="uq_v54_mailbox_credential_generation"))
    op.create_table("v54_mailbox_authority_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False), sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("mail_connection_id", sa.Uuid(as_uuid=False), nullable=False), sa.Column("principal_kind", sa.String(16), nullable=False),
        sa.Column("principal_id", sa.String(100), nullable=False), sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), server_default="revoked", nullable=False), sa.Column("authority_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("principal_kind IN ('user','service') AND state IN ('active','revoked')", name="ck_v54_mailbox_authority_state"),
        sa.CheckConstraint("authority_version > 0", name="ck_v54_mailbox_authority_version"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","mail_connection_id"], ["v54_mail_connections.organization_id","v54_mail_connections.id"], name="fk_v54_mailbox_authority_mail", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("organization_id","mail_connection_id","principal_kind","principal_id",name="uq_v54_mailbox_authority"))
    op.create_table("v54_mailbox_cutover_flags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False), sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("mail_connection_id", sa.Uuid(as_uuid=False), nullable=False), sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("shadow_write", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("shadow_read_compare", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("pilot_write", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("primary_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("actions", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("credential_generation > 0 AND record_version > 0", name="ck_v54_mailbox_flags_versions"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","mail_connection_id"], ["v54_mail_connections.organization_id","v54_mail_connections.id"], name="fk_v54_mailbox_flags_mail", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("organization_id","mail_connection_id","credential_generation",name="uq_v54_mailbox_flags_generation"))
    op.create_table("v54_mailbox_origin_decisions",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False), sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("decision_key", sa.String(200), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False), sa.Column("expected_message_version", sa.Integer(), nullable=False),
        sa.Column("expected_current_version", sa.Integer(), nullable=False), sa.Column("identity_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("mail_connection_id", sa.Uuid(as_uuid=False), nullable=False), sa.Column("binding_epoch", sa.Integer(), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False), sa.Column("source_reference_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("source_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False), sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("correlation_id", sa.String(100), nullable=False), sa.Column("decided_by_user_id", sa.Integer(), nullable=False),
        sa.Column("authority_version", sa.Integer(), nullable=False), sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expected_message_version > 0 AND expected_current_version > 0 AND binding_epoch > 0 AND credential_generation > 0 AND authority_version > 0", name="ck_v54_mailbox_decision_versions"),
        sa.CheckConstraint("outcome IN ('CONFIRM','REJECT','LEAVE_UNRESOLVED')", name="ck_v54_mailbox_decision_outcome"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","message_id"], ["messages.organization_id","messages.id"], name="fk_v54_mailbox_decision_message", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","identity_id"], ["v54_connection_identities.organization_id","v54_connection_identities.id"], name="fk_v54_mailbox_decision_identity", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","mail_connection_id"], ["v54_mail_connections.organization_id","v54_mail_connections.id"], name="fk_v54_mailbox_decision_mail", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","source_reference_id","source_version_id"], ["v54_source_versions.organization_id","v54_source_versions.source_id","v54_source_versions.id"], name="fk_v54_mailbox_decision_observation", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("organization_id","id",name="uq_v54_mailbox_decision_scope"),
        sa.UniqueConstraint("organization_id","decision_key",name="uq_v54_mailbox_decision_key"))
    op.create_table("v54_mailbox_origin_bindings",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False), sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("lineage_id", sa.Uuid(as_uuid=False), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False), sa.Column("mail_connection_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("provider_message_id", sa.String(500), nullable=True), sa.Column("source_reference_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("binding_epoch", sa.Integer(), nullable=False), sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False), sa.Column("decision_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0 AND binding_epoch > 0 AND credential_generation > 0", name="ck_v54_mailbox_binding_versions"),
        sa.CheckConstraint("state IN ('unresolved','confirmed','rejected','superseded')", name="ck_v54_mailbox_binding_state"),
        sa.CheckConstraint("(mail_connection_id IS NULL AND provider_message_id IS NULL AND source_reference_id IS NULL) OR (mail_connection_id IS NOT NULL AND provider_message_id IS NOT NULL AND source_reference_id IS NOT NULL)", name="ck_v54_mailbox_binding_origin"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","message_id"], ["messages.organization_id","messages.id"], name="fk_v54_mailbox_binding_message", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","mail_connection_id"], ["v54_mail_connections.organization_id","v54_mail_connections.id"], name="fk_v54_mailbox_binding_mail", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","source_reference_id"], ["v54_sources.organization_id","v54_sources.id"], name="fk_v54_mailbox_binding_source", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decision_id"], ["v54_mailbox_origin_decisions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("organization_id","id",name="uq_v54_mailbox_binding_scope"),
        sa.UniqueConstraint("organization_id","lineage_id","revision",name="uq_v54_mailbox_binding_revision"))
    op.create_table("v54_mailbox_origin_current",
        sa.Column("message_id", sa.Integer(), nullable=False), sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("binding_id", sa.Uuid(as_uuid=False), nullable=True), sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("record_version > 0", name="ck_v54_mailbox_current_version"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","message_id"], ["messages.organization_id","messages.id"], name="fk_v54_mailbox_current_message", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id","binding_id"], ["v54_mailbox_origin_bindings.organization_id","v54_mailbox_origin_bindings.id"], name="fk_v54_mailbox_current_binding", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("message_id"))


def downgrade():
    connection = op.get_bind()
    tables = ("v54_mailbox_origin_current","v54_mailbox_origin_bindings","v54_mailbox_origin_decisions",
              "v54_mailbox_cutover_flags","v54_mailbox_authority_states","v54_mailbox_credential_generations")
    for table in tables:
        if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} LIMIT 1)")):
            raise RuntimeError("Mailbox identity data exists; explicit archival is required")
    for table in tables: op.drop_table(table)
    op.drop_constraint("ck_v54_message_origin_version", "messages", type_="check")
    op.drop_column("messages", "origin_version")
