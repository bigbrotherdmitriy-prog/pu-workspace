from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    counterparty: Mapped[str | None] = mapped_column(String(500))
    contract_kind: Mapped[str] = mapped_column(String(30), default="customer", server_default="customer", index=True)
    parent_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    advance_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    retention_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    warranty_until: Mapped[date | None] = mapped_column(Date)
    signed_at: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(50), default="active", index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
