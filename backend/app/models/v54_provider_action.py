"""Wave 3 provider action records."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    Integer, JSON, String, UniqueConstraint, event, inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProviderAction(Base):
    __tablename__ = "v54_provider_actions"
    __table_args__ = (
        UniqueConstraint("organization_id", "action_id", "revision", name="uq_v54_provider_action_scope"),
        UniqueConstraint("organization_id", "mailbox_key", "command_key", name="uq_v54_provider_command"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_v54_provider_idempotency"),
        CheckConstraint("revision > 0 AND organization_id > 0 AND project_id > 0", name="ck_v54_provider_action_scope"),
        CheckConstraint("mode = 'CONFIRM' AND synthetic_only = true AND provider = 'synthetic'",
                        name="ck_v54_provider_confirm_synthetic"),
        CheckConstraint("length(payload_hash) = 64 AND length(mailbox_key) = 64 AND length(envelope_hash) = 64",
                        name="ck_v54_provider_action_hashes"),
        CheckConstraint("state IN ('FROZEN','READY','EXECUTING','APPLIED','NOT_APPLIED','UNKNOWN','BLOCKED')",
                        name="ck_v54_provider_action_state"),
    )
    action_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[int] = mapped_column(Integer)
    mailbox_key: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(10))
    synthetic_only: Mapped[bool] = mapped_column(Boolean)
    action_kind: Mapped[str] = mapped_column(String(60))
    reversibility: Mapped[str] = mapped_column(String(20))
    payload_hash: Mapped[str] = mapped_column(String(64))
    command_key: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    context_revision: Mapped[int] = mapped_column(Integer)
    evidence_pins: Mapped[list] = mapped_column(JSON)
    authority_epoch: Mapped[int] = mapped_column(Integer)
    capability_version: Mapped[int] = mapped_column(Integer)
    credential_generation: Mapped[int] = mapped_column(Integer)
    relation_kind: Mapped[str | None] = mapped_column(String(20))
    relation_action_id: Mapped[str | None] = mapped_column(String(100))
    envelope_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="FROZEN")
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderActionApproval(Base):
    __tablename__ = "v54_provider_action_approvals"
    __table_args__ = (
        UniqueConstraint("action_id", "revision", name="uq_v54_provider_action_approval"),
        UniqueConstraint("id", "organization_id", "action_id", "revision",
                         name="uq_v54_provider_approval_binding"),
        ForeignKeyConstraint(
            ["organization_id", "action_id", "revision"],
            ["v54_provider_actions.organization_id", "v54_provider_actions.action_id",
             "v54_provider_actions.revision"],
            name="fk_v54_provider_approval_action", ondelete="RESTRICT",
        ),
        CheckConstraint("state IN ('GRANTED','REVOKED','EXPIRED')", name="ck_v54_provider_approval_state"),
        CheckConstraint("length(payload_hash) = 64 AND length(mailbox_key) = 64 AND length(envelope_hash) = 64",
                        name="ck_v54_provider_approval_hashes"),
    )
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer)
    organization_id: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[int] = mapped_column(Integer)
    mailbox_key: Mapped[str] = mapped_column(String(64))
    command_key: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    envelope_hash: Mapped[str] = mapped_column(String(64))
    authority_epoch: Mapped[int] = mapped_column(Integer)
    capability_version: Mapped[int] = mapped_column(Integer)
    credential_generation: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="GRANTED")
    approved_by: Mapped[str] = mapped_column(String(100))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderDispatchOutbox(Base):
    __tablename__ = "v54_provider_dispatch_outbox"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "action_id", "revision"],
            ["v54_provider_actions.organization_id", "v54_provider_actions.action_id",
             "v54_provider_actions.revision"],
            name="fk_v54_provider_outbox_action", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approval_id", "organization_id", "action_id", "revision"],
            ["v54_provider_action_approvals.id", "v54_provider_action_approvals.organization_id",
             "v54_provider_action_approvals.action_id", "v54_provider_action_approvals.revision"],
            name="fk_v54_provider_outbox_approval", ondelete="RESTRICT",
        ),
        Index("ix_v54_provider_outbox_pending", "pending", "organization_id"),
    )
    action_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer)
    approval_id: Mapped[str] = mapped_column(String(100))
    envelope_hash: Mapped[str] = mapped_column(String(64))
    pending: Mapped[bool] = mapped_column(Boolean, default=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("background_jobs.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderExecutionAttempt(Base):
    __tablename__ = "v54_provider_execution_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_v54_provider_attempt_id"),
        ForeignKeyConstraint(
            ["organization_id", "action_id", "revision"],
            ["v54_provider_actions.organization_id", "v54_provider_actions.action_id",
             "v54_provider_actions.revision"],
            name="fk_v54_provider_attempt_action", ondelete="RESTRICT",
        ),
        CheckConstraint("state IN ('DISPATCHING','APPLIED','NOT_APPLIED','UNKNOWN')", name="ck_v54_provider_attempt_state"),
    )
    action_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(100))
    organization_id: Mapped[int] = mapped_column(Integer)
    first_job_id: Mapped[int] = mapped_column(ForeignKey("background_jobs.id", ondelete="RESTRICT"))
    adapter_name: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderOutcomeObservation(Base):
    """Append-only provider result fact; later facts never rewrite earlier ones."""

    __tablename__ = "v54_provider_outcome_observations"
    __table_args__ = (
        UniqueConstraint("action_id", "revision", "sequence", name="uq_v54_provider_observation_sequence"),
        ForeignKeyConstraint(
            ["organization_id", "action_id", "revision"],
            ["v54_provider_actions.organization_id", "v54_provider_actions.action_id",
             "v54_provider_actions.revision"],
            name="fk_v54_provider_observation_action", ondelete="RESTRICT",
        ),
        CheckConstraint("outcome IN ('APPLIED','NOT_APPLIED','UNKNOWN')", name="ck_v54_provider_observation_outcome"),
        CheckConstraint("source IN ('DISPATCH','RECONCILE','LATE_RECEIPT','PROCESS_RECOVERY')",
                        name="ck_v54_provider_observation_source"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer)
    organization_id: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer)
    attempt_id: Mapped[str] = mapped_column(String(100))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("background_jobs.id", ondelete="RESTRICT"))
    mailbox_key: Mapped[str] = mapped_column(String(64))
    command_key: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    envelope_hash: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(20))
    retry_safe: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(30))
    late: Mapped[bool] = mapped_column(Boolean, default=False)
    external_ref: Mapped[str | None] = mapped_column(String(200))
    safe_code: Mapped[str | None] = mapped_column(String(60))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _immutable_action(_mapper, _connection, target):
    if any(attr.history.has_changes() and attr.key != "state" for attr in inspect(target).attrs):
        raise ValueError("immutable_provider_action")


def _immutable_approval(_mapper, _connection, target):
    if any(attr.history.has_changes() and attr.key != "state" for attr in inspect(target).attrs):
        raise ValueError("immutable_provider_approval")


def _deny_observation_change(*_args, **_kwargs):
    raise ValueError("append_only_provider_observation")


event.listen(ProviderAction, "before_update", _immutable_action)
event.listen(ProviderActionApproval, "before_update", _immutable_approval)
event.listen(ProviderOutcomeObservation, "before_update", _deny_observation_change)
event.listen(ProviderOutcomeObservation, "before_delete", _deny_observation_change)
