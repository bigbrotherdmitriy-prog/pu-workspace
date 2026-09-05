from datetime import date, datetime, time as dt_time

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, Time, UniqueConstraint, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (CheckConstraint("record_version > 0", name="ck_meetings_record_version"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(500))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    participants: Mapped[str | None] = mapped_column(Text, nullable=True)
    agenda: Mapped[str | None] = mapped_column(Text, nullable=True)
    minutes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
        CheckConstraint("record_version > 0", name="ck_management_history_record_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    record_version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    old_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    new_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

