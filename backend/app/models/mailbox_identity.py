"""Durable mailbox identity and default-off cutover state."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, JSON, String, UniqueConstraint, Uuid, event, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _id(): return str(uuid4())


class MailboxCredentialGeneration(Base):
    __tablename__ = "v54_mailbox_credential_generations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_v54_mailbox_credential_scope"),
        UniqueConstraint("organization_id", "connection_identity_id", "generation", name="uq_v54_mailbox_credential_generation"),
        ForeignKeyConstraint(["organization_id", "connection_identity_id"], ["v54_connection_identities.organization_id", "v54_connection_identities.id"], ondelete="RESTRICT", name="fk_v54_mailbox_credential_identity"),
        CheckConstraint("generation > 0 AND binding_epoch > 0", name="ck_v54_mailbox_credential_positive"),
        CheckConstraint("state IN ('active','expired','revoked')", name="ck_v54_mailbox_credential_state"),
        CheckConstraint("(google_token_id IS NOT NULL) != (integration_credential_id IS NOT NULL)", name="ck_v54_mailbox_credential_owner"),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_id)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    connection_identity_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    generation: Mapped[int] = mapped_column()
    binding_epoch: Mapped[int] = mapped_column()
    google_token_id: Mapped[int | None] = mapped_column(ForeignKey("google_oauth_tokens.id", ondelete="RESTRICT"))
    integration_credential_id: Mapped[int | None] = mapped_column(ForeignKey("integration_credentials.id", ondelete="RESTRICT"))
    state: Mapped[str] = mapped_column(String(16), server_default="active")
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MailboxOriginDecision(Base):
    __tablename__ = "v54_mailbox_origin_decisions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_v54_mailbox_decision_scope"),
        UniqueConstraint("organization_id", "decision_key", name="uq_v54_mailbox_decision_key"),
        ForeignKeyConstraint(["organization_id", "message_id"], ["messages.organization_id", "messages.id"], ondelete="RESTRICT", name="fk_v54_mailbox_decision_message"),
        ForeignKeyConstraint(["organization_id", "identity_id"], ["v54_connection_identities.organization_id", "v54_connection_identities.id"], ondelete="RESTRICT", name="fk_v54_mailbox_decision_identity"),
        ForeignKeyConstraint(["organization_id", "mail_connection_id"], ["v54_mail_connections.organization_id", "v54_mail_connections.id"], ondelete="RESTRICT", name="fk_v54_mailbox_decision_mail"),
        ForeignKeyConstraint(["organization_id", "source_reference_id", "source_version_id"], ["v54_source_versions.organization_id", "v54_source_versions.source_id", "v54_source_versions.id"], ondelete="RESTRICT", name="fk_v54_mailbox_decision_observation"),
        CheckConstraint("expected_message_version > 0 AND expected_current_version > 0 AND identity_record_version > 0 AND mail_connection_record_version > 0 AND binding_epoch > 0 AND credential_generation > 0 AND source_reference_record_version > 0 AND source_version_revision = 1 AND authority_version > 0", name="ck_v54_mailbox_decision_versions"),
        CheckConstraint("outcome IN ('CONFIRM','REJECT','LEAVE_UNRESOLVED')", name="ck_v54_mailbox_decision_outcome"),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_id)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    decision_key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[int] = mapped_column()
    expected_message_version: Mapped[int] = mapped_column()
    expected_current_version: Mapped[int] = mapped_column()
    identity_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    identity_record_version: Mapped[int] = mapped_column()
    mail_connection_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    mail_connection_record_version: Mapped[int] = mapped_column()
    binding_epoch: Mapped[int] = mapped_column()
    credential_generation: Mapped[int] = mapped_column()
    source_reference_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    source_reference_record_version: Mapped[int] = mapped_column()
    source_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    source_version_revision: Mapped[int] = mapped_column()
    evidence_refs: Mapped[list] = mapped_column(JSON)
    reason_code: Mapped[str] = mapped_column(String(50))
    correlation_id: Mapped[str] = mapped_column(String(100))
    decided_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    authority_version: Mapped[int] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MailboxOriginBinding(Base):
    __tablename__ = "v54_mailbox_origin_bindings"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_v54_mailbox_binding_scope"),
        UniqueConstraint("organization_id", "lineage_id", "revision", name="uq_v54_mailbox_binding_revision"),
        ForeignKeyConstraint(["organization_id", "message_id"], ["messages.organization_id", "messages.id"], ondelete="RESTRICT", name="fk_v54_mailbox_binding_message"),
        ForeignKeyConstraint(["organization_id", "mail_connection_id"], ["v54_mail_connections.organization_id", "v54_mail_connections.id"], ondelete="RESTRICT", name="fk_v54_mailbox_binding_mail"),
        ForeignKeyConstraint(["organization_id", "source_reference_id"], ["v54_sources.organization_id", "v54_sources.id"], ondelete="RESTRICT", name="fk_v54_mailbox_binding_source"),
        CheckConstraint("revision > 0 AND binding_epoch > 0 AND credential_generation > 0", name="ck_v54_mailbox_binding_versions"),
        CheckConstraint("state IN ('unresolved','confirmed','rejected','superseded')", name="ck_v54_mailbox_binding_state"),
        CheckConstraint("(mail_connection_id IS NULL AND provider_message_id IS NULL AND source_reference_id IS NULL) OR (mail_connection_id IS NOT NULL AND provider_message_id IS NOT NULL AND source_reference_id IS NOT NULL)", name="ck_v54_mailbox_binding_origin"),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_id)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    lineage_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), default=_id)
    revision: Mapped[int] = mapped_column()
    message_id: Mapped[int] = mapped_column()
    mail_connection_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    provider_message_id: Mapped[str | None] = mapped_column(String(500))
    source_reference_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    binding_epoch: Mapped[int] = mapped_column()
    credential_generation: Mapped[int] = mapped_column()
    state: Mapped[str] = mapped_column(String(20))
    decision_id: Mapped[str] = mapped_column(ForeignKey("v54_mailbox_origin_decisions.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MailboxOriginCurrent(Base):
    __tablename__ = "v54_mailbox_origin_current"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "message_id"], ["messages.organization_id", "messages.id"], ondelete="RESTRICT", name="fk_v54_mailbox_current_message"),
        ForeignKeyConstraint(["organization_id", "binding_id"], ["v54_mailbox_origin_bindings.organization_id", "v54_mailbox_origin_bindings.id"], ondelete="RESTRICT", name="fk_v54_mailbox_current_binding"),
        CheckConstraint("record_version > 0", name="ck_v54_mailbox_current_version"),
    )
    message_id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    binding_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    record_version: Mapped[int] = mapped_column(server_default="1")


class MailboxAuthorityState(Base):
    __tablename__ = "v54_mailbox_authority_states"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "mail_connection_id"], ["v54_mail_connections.organization_id", "v54_mail_connections.id"], ondelete="RESTRICT", name="fk_v54_mailbox_authority_mail"),
        UniqueConstraint("organization_id", "mail_connection_id", "principal_kind", "principal_id", name="uq_v54_mailbox_authority"),
        CheckConstraint("principal_kind IN ('user','service') AND state IN ('active','revoked')", name="ck_v54_mailbox_authority_state"),
        CheckConstraint("authority_version > 0", name="ck_v54_mailbox_authority_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    mail_connection_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    principal_kind: Mapped[str] = mapped_column(String(16))
    principal_id: Mapped[str] = mapped_column(String(100))
    permissions: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), server_default="revoked")
    authority_version: Mapped[int] = mapped_column(server_default="1")
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MailboxCutoverFlags(Base):
    __tablename__ = "v54_mailbox_cutover_flags"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "mail_connection_id"], ["v54_mail_connections.organization_id", "v54_mail_connections.id"], ondelete="RESTRICT", name="fk_v54_mailbox_flags_mail"),
        UniqueConstraint("organization_id", "mail_connection_id", "credential_generation", name="uq_v54_mailbox_flags_generation"),
        CheckConstraint("credential_generation > 0 AND record_version > 0", name="ck_v54_mailbox_flags_versions"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    mail_connection_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    credential_generation: Mapped[int] = mapped_column()
    shadow_write: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    shadow_read_compare: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    pilot_write: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    primary_read: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    actions: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    record_version: Mapped[int] = mapped_column(server_default="1")


def _deny_change(*_args, **_kwargs): raise ValueError("append_only_record")


for _model in (MailboxCredentialGeneration, MailboxOriginDecision, MailboxOriginBinding):
    event.listen(_model, "before_update", _deny_change)
    event.listen(_model, "before_delete", _deny_change)
