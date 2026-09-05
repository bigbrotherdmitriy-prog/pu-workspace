from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduleBaseline(Base):
    __tablename__ = "schedule_baselines"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_schedule_baseline_version"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduleItem(Base):
    __tablename__ = "schedule_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    baseline_id: Mapped[int] = mapped_column(ForeignKey("schedule_baselines.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    planned_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_finish: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    actual_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_finish: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_progress: Mapped[float] = mapped_column(Float, default=0)
    actual_progress: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    source_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetLine(Base):
    __tablename__ = "budget_lines"
    __table_args__ = (
        CheckConstraint("record_version > 0", name="ck_budget_line_record_version"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_budget_line_confidence"),
        CheckConstraint("review_status IN ('pending_confirmation','required','confirmed','rejected')", name="ck_budget_line_review_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    schedule_item_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_items.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    source_document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=True, index=True)
    evidence_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    evidence_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_assessment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(String(30), default="pending_confirmation", server_default="pending_confirmation", index=True)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    category: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(String(1000))
    planned_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    committed_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    forecast_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    source_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CashFlowEntry(Base):
    __tablename__ = "cash_flow_entries"
    __table_args__ = (
        CheckConstraint("record_version > 0", name="ck_cash_flow_record_version"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_cash_flow_confidence"),
        CheckConstraint("review_status IN ('pending_confirmation','required','confirmed','rejected')", name="ck_cash_flow_review_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    schedule_item_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_items.id", ondelete="SET NULL"), nullable=True, index=True)
    budget_line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_lines.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    source_document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=True, index=True)
    evidence_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    evidence_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_assessment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(String(30), default="pending_confirmation", server_default="pending_confirmation", index=True)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(500))
    planned_date: Mapped[date] = mapped_column(Date, index=True)
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    counterparty: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    source_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CashFlowFactHistory(Base):
    """Immutable human-confirmed fact history; it never represents a bank action."""

    __tablename__ = "cash_flow_fact_history"
    __table_args__ = (
        UniqueConstraint("cash_flow_entry_id", "sequence", name="uq_cash_flow_fact_history_sequence"),
        CheckConstraint("sequence > 0 AND resulting_record_version > 0", name="ck_cash_flow_fact_history_version"),
        CheckConstraint("event IN ('confirmed','corrected')", name="ck_cash_flow_fact_history_event"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cash_flow_entry_id: Mapped[int] = mapped_column(ForeignKey("cash_flow_entries.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(20))
    previous_actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    previous_actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    resulting_actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    resulting_actual_date: Mapped[date] = mapped_column(Date)
    resulting_record_version: Mapped[int] = mapped_column(Integer)
    changed_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _deny_cash_flow_fact_history_mutation(_mapper, _connection, _target):
    raise ValueError("immutable_cash_flow_fact_history")


event.listen(CashFlowFactHistory, "before_update", _deny_cash_flow_fact_history_mutation)
event.listen(CashFlowFactHistory, "before_delete", _deny_cash_flow_fact_history_mutation)


class ProcurementItem(Base):
    __tablename__ = "procurement_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    supplier: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stage: Mapped[str] = mapped_column(String(30), default="request", index=True)
    planned_delivery: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    actual_delivery: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    source_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AcceptanceAct(Base):
    __tablename__ = "acceptance_acts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    act_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    source_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
