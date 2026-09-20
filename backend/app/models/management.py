from datetime import date, datetime, time as dt_time
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint, Integer, JSON, Numeric, String, Text, Time, UniqueConstraint, Uuid, event, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Obligation(Base):
    __tablename__ = "obligations"
    __table_args__ = (
        UniqueConstraint("project_id", "source_id", "source_hash", name="uq_obligation_source"),
        CheckConstraint("record_version > 0", name="ck_obligations_record_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), default="needs_confirmation", index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str] = mapped_column(String(500))
    source_name: Mapped[str] = mapped_column(String(1000))
    source_excerpt: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    # Mirrors Task's LLM extraction fields -- see migration d29a6c4f1e83.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    amount_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    amount_evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date_evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_hint: Mapped[str | None] = mapped_column(String(300), nullable=True)
    assignee_evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(20), default="regex", server_default="regex")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint("record_version > 0", name="ck_meetings_record_version"),
        CheckConstraint(
            "duration_minutes IS NULL OR (duration_minutes >= 1 AND duration_minutes <= 10080)",
            name="ck_meetings_duration_minutes",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(500))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    participants: Mapped[str | None] = mapped_column(Text, nullable=True)
    agenda: Mapped[str | None] = mapped_column(Text, nullable=True)
    minutes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND contact_id IS NULL) OR "
            "(user_id IS NULL AND contact_id IS NOT NULL)",
            name="ck_meeting_participants_one_identity",
        ),
        UniqueConstraint("meeting_id", "user_id", name="uq_meeting_participant_user"),
        UniqueConstraint("meeting_id", "contact_id", name="uq_meeting_participant_contact"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_contacts.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeetingSourceBinding(Base):
    """Immutable declaration that one meeting revision uses one exact source observation."""

    __tablename__ = "meeting_source_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            ["v54_source_versions.organization_id", "v54_source_versions.source_id", "v54_source_versions.id"],
            name="fk_meeting_source_binding_observation", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_id", "source_id", "source_version_id"],
            ["v54_evidence.organization_id", "v54_evidence.id", "v54_evidence.source_id", "v54_evidence.source_version_id"],
            name="fk_meeting_source_binding_evidence", ondelete="RESTRICT",
        ),
        UniqueConstraint("meeting_id", "meeting_record_version", name="uq_meeting_source_binding_version"),
        UniqueConstraint("meeting_id", "command_id", name="uq_meeting_source_binding_command"),
        CheckConstraint("meeting_record_version > 1", name="ck_meeting_source_binding_version"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="RESTRICT"), index=True)
    meeting_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    source_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    evidence_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    materialization_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("v54_materializations.id", ondelete="RESTRICT"), nullable=False,
    )
    command_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    command_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    bound_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeetingProposal(Base):
    """Reviewable task/risk/decision candidate; never an external or durable action."""

    __tablename__ = "meeting_proposals"
    __table_args__ = (
        UniqueConstraint("binding_id", "fingerprint", name="uq_meeting_proposal_fingerprint"),
        CheckConstraint("record_version > 0", name="ck_meeting_proposal_version"),
        CheckConstraint("proposal_type IN ('task','risk','decision')", name="ck_meeting_proposal_type"),
        CheckConstraint("status IN ('proposed','confirmed')", name="ck_meeting_proposal_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="RESTRICT"), index=True)
    binding_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("meeting_source_bindings.id", ondelete="RESTRICT"), index=True,
    )
    proposal_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="proposed", index=True)
    confirmation_command_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)
    confirmation_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _deny_meeting_source_binding_mutation(_mapper, _connection, _target):
    raise ValueError("meeting_source_binding_is_append_only")


event.listen(MeetingSourceBinding, "before_update", _deny_meeting_source_binding_mutation)
event.listen(MeetingSourceBinding, "before_delete", _deny_meeting_source_binding_mutation)


class BookableResource(Base):
    __tablename__ = "bookable_resources"
    __table_args__ = (
        ForeignKeyConstraint(
            ("organization_id", "managing_project_id"),
            ("projects.organization_id", "projects.id"),
            name="fk_bookable_resources_managing_project_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint("record_version > 0", name="ck_bookable_resources_record_version"),
        CheckConstraint(
            "kind IN ('room', 'equipment', 'other')",
            name="ck_bookable_resources_kind",
        ),
        CheckConstraint(
            "capacity IS NULL OR capacity > 0",
            name="ck_bookable_resources_capacity",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    managing_project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, server_default="Europe/Moscow")
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class MeetingResource(Base):
    __tablename__ = "meeting_resources"
    __table_args__ = (
        UniqueConstraint("meeting_id", "resource_id", name="uq_meeting_resource"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    resource_id: Mapped[int] = mapped_column(
        ForeignKey("bookable_resources.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notification_user_key"),
        CheckConstraint("record_version > 0", name="ck_notifications_record_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(index=True)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationPolicy(Base):
    __tablename__ = "notification_policies"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_notification_policy_project_user"),
        CheckConstraint("record_version > 0", name="ck_notification_policy_record_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, server_default="Europe/Moscow")
    deadline_local_time: Mapped[dt_time] = mapped_column(Time, nullable=False, server_default="09:00:00")
    quiet_start: Mapped[dt_time] = mapped_column(Time, nullable=False, server_default="22:00:00")
    quiet_end: Mapped[dt_time] = mapped_column(Time, nullable=False, server_default="07:00:00")
    escalation_delays: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: [0, 60, 240])
    channels: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["in_app"])
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ManagementHistory(Base):
    """Append-only audit trail for human-visible MVP3 state transitions."""

    __tablename__ = "management_history"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "entity_type", "idempotency_key",
            name="uq_management_history_idempotency",
        ),
        CheckConstraint("record_version > 0", name="ck_management_history_record_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    record_version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    # Optional command identity for replay-safe human decisions. Existing
    # history writers keep these NULL; a supplied key is tenant-unique for an
    # entity type, so it cannot be reused for a different contact.
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    command_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    old_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    new_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

