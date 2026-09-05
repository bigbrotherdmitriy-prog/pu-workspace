from datetime import date, datetime, time

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, Time, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Obligation(Base):
    __tablename__ = "obligations"
    __table_args__ = (
        UniqueConstraint("project_id", "source_id", "source_hash", name="uq_obligation_source"),
        CheckConstraint("record_version > 0 AND escalation_level >= 0", name="ck_obligation_lifecycle_version"),
        CheckConstraint("review_state IN ('unverified','needs_review','verified')", name="ck_obligation_review_state"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), default="needs_confirmation", index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/Moscow", server_default="Europe/Moscow")
    deadline_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str] = mapped_column(String(500))
    source_name: Mapped[str] = mapped_column(String(1000))
    source_excerpt: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    evidence_pins: Mapped[list | None] = mapped_column(JSON, nullable=True)
    review_state: Mapped[str] = mapped_column(String(30), default="unverified", server_default="unverified", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ObligationHistory(Base):
    __tablename__ = "obligation_history"
    __table_args__ = (UniqueConstraint("obligation_id", "sequence", name="uq_obligation_history_sequence"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40))
    resulting_version: Mapped[int] = mapped_column(Integer)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_pins: Mapped[list | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _deny_obligation_history_mutation(mapper, connection, target):
    raise ValueError("management_history_is_append_only")


event.listen(ObligationHistory, "before_update", _deny_obligation_history_mutation)
event.listen(ObligationHistory, "before_delete", _deny_obligation_history_mutation)


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
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
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key", name="uq_notification_user_key"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
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

