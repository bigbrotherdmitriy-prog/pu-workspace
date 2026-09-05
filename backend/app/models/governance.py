from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Risk(Base):
    __tablename__ = "risks"
    __table_args__ = (
        UniqueConstraint("project_id", "source_hash", name="uq_risk_source_hash"),
        CheckConstraint("record_version > 0", name="ck_risk_record_version"),
        CheckConstraint("review_state IN ('unverified','needs_review','verified')", name="ck_risk_review_state"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    obligation_id: Mapped[int | None] = mapped_column(ForeignKey("obligations.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30), default="risk", index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    criticality: Mapped[str] = mapped_column(String(30), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(50), default="needs_confirmation", index=True)
    action_note: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        UniqueConstraint("project_id", "source_hash", name="uq_decision_source_hash"),
        CheckConstraint("record_version > 0", name="ck_decision_record_version"),
        CheckConstraint("review_state IN ('unverified','needs_review','verified')", name="ck_decision_review_state"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    initiator_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True)
    obligation_id: Mapped[int | None] = mapped_column(ForeignKey("obligations.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    risk_id: Mapped[int | None] = mapped_column(ForeignKey("risks.id", ondelete="SET NULL"), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="needs_confirmation", index=True)
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


class GovernanceHistory(Base):
    __tablename__ = "governance_history"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "sequence", name="uq_governance_history_sequence"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_status: Mapped[str] = mapped_column(String(50))
    resulting_version: Mapped[int] = mapped_column(Integer)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_pins: Mapped[list | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _deny_governance_history_mutation(mapper, connection, target):
    raise ValueError("management_history_is_append_only")


event.listen(GovernanceHistory, "before_update", _deny_governance_history_mutation)
event.listen(GovernanceHistory, "before_delete", _deny_governance_history_mutation)
