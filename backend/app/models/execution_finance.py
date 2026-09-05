from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
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
    source_format: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduleItem(Base):
    __tablename__ = "schedule_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    baseline_id: Mapped[int] = mapped_column(ForeignKey("schedule_baselines.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_items.id", ondelete="SET NULL"), nullable=True, index=True)
    duration_days: Mapped[int] = mapped_column(Integer, default=1)
    is_milestone: Mapped[bool] = mapped_column(default=False)
    predecessor_ids: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    constraint_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    constraint_date: Mapped[date | None] = mapped_column(Date, nullable=True)
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
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
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
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    schedule_item_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_items.id", ondelete="SET NULL"), nullable=True, index=True)
    budget_line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_lines.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    source_document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=True, index=True)
    source_document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(500))
    planned_date: Mapped[date] = mapped_column(Date, index=True)
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    counterparty: Mapped[str | None] = mapped_column(String(500), nullable=True)
    object_name: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    source_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
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
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    source_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaymentEvent(Base):
    """Immutable source of truth for payment facts and their compensations.

    ``CashFlowEntry.actual_*`` remains a backwards-compatible projection.  Rows in
    this table are never updated: a correction or reversal points at the event it
    supersedes and produces a new projection.
    """

    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("cash_flow_entry_id", "idempotency_key", name="uq_payment_event_idempotency"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    cash_flow_entry_id: Mapped[int] = mapped_column(ForeignKey("cash_flow_entries.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(20), index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    supersedes_event_id: Mapped[int | None] = mapped_column(ForeignKey("payment_events.id", ondelete="RESTRICT"), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    source_document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=True, index=True)
    source_document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
