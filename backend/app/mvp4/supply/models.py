"""Additive supply-control persistence contract.

These models are intentionally not imported by ``app.models`` until the schema
owner supplies the sequential Alembic migration described in the audit.  The
module neither replaces the legacy execution-finance registers nor creates a
second action/queue system.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


SUPPLY_STATES = (
    "needs_review",
    "request_pending_approval",
    "request_rejected",
    "request_approved",
    "order_draft",
    "order_approved",
    "order_recorded",
    "partially_delivered",
    "delivered",
    "delivery_discrepancy",
    "act_pending_approval",
    "partially_accepted",
    "accepted",
    "cancelled",
)


class SupplyCase(Base):
    __tablename__ = "mvp4_supply_cases"
    __table_args__ = (
        UniqueConstraint("organization_id", "project_id", "request_key", name="uq_mvp4_supply_request_key"),
        ForeignKeyConstraint(
            ["organization_id", "evidence_id", "source_id", "source_version_id"],
            [
                "v54_evidence.organization_id",
                "v54_evidence.id",
                "v54_evidence.source_id",
                "v54_evidence.source_version_id",
            ],
            name="fk_mvp4_supply_exact_evidence",
            ondelete="RESTRICT",
        ),
        CheckConstraint("record_version > 0", name="ck_mvp4_supply_record_version"),
        CheckConstraint("evidence_revision = 1", name="ck_mvp4_supply_evidence_revision"),
        CheckConstraint("requested_quantity > 0", name="ck_mvp4_supply_requested_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_mvp4_supply_unit_price"),
        CheckConstraint("ordered_quantity >= 0", name="ck_mvp4_supply_ordered_quantity"),
        CheckConstraint("delivered_quantity >= 0", name="ck_mvp4_supply_delivered_quantity"),
        CheckConstraint("accepted_quantity >= 0", name="ck_mvp4_supply_accepted_quantity"),
        CheckConstraint("pending_acceptance_quantity >= 0", name="ck_mvp4_supply_pending_acceptance_quantity"),
        CheckConstraint(
            "status IN (" + ",".join(f"'{state}'" for state in SUPPLY_STATES) + ")",
            name="ck_mvp4_supply_status",
        ),
        CheckConstraint(
            "review_state IN ('needs_review','verified','rejected')",
            name="ck_mvp4_supply_review_state",
        ),
        CheckConstraint(
            "external_action_status = 'not_created'",
            name="ck_mvp4_supply_no_external_action",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="RESTRICT"), index=True)
    schedule_baseline_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_baselines.id", ondelete="RESTRICT"), index=True
    )
    schedule_baseline_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_item_id: Mapped[int] = mapped_column(ForeignKey("schedule_items.id", ondelete="RESTRICT"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), index=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), index=True
    )
    evidence_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
    evidence_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    source_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    source_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    request_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    supplier: Mapped[str] = mapped_column(String(500), nullable=False)
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB", server_default="RUB")
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    review_state: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    evidence_confidence: Mapped[float | None] = mapped_column(nullable=True)
    ordered_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False, default=0, server_default="0")
    delivered_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False, default=0, server_default="0")
    accepted_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False, default=0, server_default="0")
    pending_acceptance_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), nullable=False, default=0, server_default="0"
    )
    order_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    act_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    discrepancy_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    discrepancy_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    external_action_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_created", server_default="not_created"
    )
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    request_approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    order_approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    act_approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SupplyCaseVersion(Base):
    """Immutable business snapshot; survives later workflow transitions."""

    __tablename__ = "mvp4_supply_case_versions"
    __table_args__ = (
        UniqueConstraint("supply_case_id", "sequence", name="uq_mvp4_supply_version_sequence"),
        CheckConstraint("sequence > 0 AND resulting_record_version > 0", name="ck_mvp4_supply_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_case_id: Mapped[int] = mapped_column(ForeignKey("mvp4_supply_cases.id", ondelete="RESTRICT"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    resulting_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_pin: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SupplyCommandReceipt(Base):
    """Immutable idempotency receipt scoped to one supply case."""

    __tablename__ = "mvp4_supply_command_receipts"
    __table_args__ = (
        UniqueConstraint("supply_case_id", "command_key", name="uq_mvp4_supply_command_key"),
        CheckConstraint("resulting_record_version > 0", name="ck_mvp4_supply_receipt_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_case_id: Mapped[int] = mapped_column(ForeignKey("mvp4_supply_cases.id", ondelete="RESTRICT"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    command_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    result_status: Mapped[str] = mapped_column(String(40), nullable=False)
    resulting_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _deny_immutable_mutation(_mapper, _connection, _target):
    raise ValueError("supply_history_is_append_only")


for _model in (SupplyCaseVersion, SupplyCommandReceipt):
    event.listen(_model, "before_update", _deny_immutable_mutation)
    event.listen(_model, "before_delete", _deny_immutable_mutation)
