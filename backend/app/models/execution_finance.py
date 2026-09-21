from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
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


class CostCategory(Base):
    __tablename__ = "cost_categories"
    __table_args__ = (
        UniqueConstraint("organization_id", "normalized_name", name="uq_cost_categories_org_name"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True,
    )
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class BudgetLine(Base):
    __tablename__ = "budget_lines"
    __table_args__ = (
        CheckConstraint(
            "(source_document_version_id IS NULL AND source_document_sha256 IS NULL) OR "
            "(source_document_version_id IS NOT NULL AND source_document_sha256 IS NOT NULL)",
            name="ck_budget_line_source_pin_pair",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    cost_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("cost_categories.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    category: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(String(1000))
    planned_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    committed_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    forecast_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    source_document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    source_document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    cost_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("cost_categories.id", ondelete="SET NULL"), nullable=True, index=True,
    )
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


class InvoiceExtractionProposal(Base):
    __tablename__ = "invoice_extraction_proposals"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_document_version_id",
            name="uq_invoice_extraction_project_version",
        ),
        CheckConstraint(
            "status IN ('proposed','confirmed','rejected')",
            name="ck_invoice_extraction_status",
        ),
        CheckConstraint(
            "target_kind IN ('cash_flow','budget')",
            name="ck_invoice_extraction_target_kind",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True,
    )
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), index=True,
    )
    source_document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), index=True,
    )
    source_document_sha256: Mapped[str] = mapped_column(String(64))
    proposed_cost_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("cost_categories.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    selected_cost_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("cost_categories.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    amount_evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", server_default="RUB")
    counterparty: Mapped[str | None] = mapped_column(String(500), nullable=True)
    counterparty_evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_purpose: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    payment_purpose_evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    extraction_method: Mapped[str] = mapped_column(String(30))
    fallback_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_kind: Mapped[str] = mapped_column(String(20), default="cash_flow", server_default="cash_flow")
    status: Mapped[str] = mapped_column(String(30), default="proposed", server_default="proposed", index=True)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_cash_flow_id: Mapped[int | None] = mapped_column(
        ForeignKey("cash_flow_entries.id", ondelete="SET NULL"), nullable=True,
    )
    created_budget_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget_lines.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class ContractBudgetProposal(Base):
    __tablename__ = "contract_budget_proposals"
    __table_args__ = (
        UniqueConstraint("contract_id", "contract_record_version", name="uq_contract_budget_proposal_version"),
        CheckConstraint("operation IN ('create','revise')", name="ck_contract_budget_proposal_operation"),
        CheckConstraint("status IN ('proposed','confirmed','rejected','superseded')", name="ck_contract_budget_proposal_status"),
        CheckConstraint(
            "(source_document_id IS NULL AND source_document_version_id IS NULL AND source_document_sha256 IS NULL) OR "
            "(source_document_id IS NOT NULL AND source_document_version_id IS NOT NULL AND source_document_sha256 IS NOT NULL)",
            name="ck_contract_budget_proposal_source_pin",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    contract_record_version: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    advance_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    retention_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", server_default="RUB")
    description: Mapped[str] = mapped_column(String(1000))
    selected_cost_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("cost_categories.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    source_document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    source_document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    target_budget_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget_lines.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    created_budget_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget_lines.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="proposed", server_default="proposed", index=True)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


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
    __table_args__ = (
        CheckConstraint(
            "(source_document_version_id IS NULL AND source_document_sha256 IS NULL) OR "
            "(source_document_version_id IS NOT NULL AND source_document_sha256 IS NOT NULL)",
            name="ck_acceptance_act_source_pin_pair",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    budget_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget_lines.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    source_document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    source_document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
