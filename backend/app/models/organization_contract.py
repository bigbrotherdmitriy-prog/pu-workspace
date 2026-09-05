from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(500))
    inn: Mapped[str | None] = mapped_column(String(12), index=True)
    kpp: Mapped[str | None] = mapped_column(String(9))
    ogrn: Mapped[str | None] = mapped_column(String(15))
    okpo: Mapped[str | None] = mapped_column(String(14))
    okato: Mapped[str | None] = mapped_column(String(20))
    oktmo: Mapped[str | None] = mapped_column(String(20))
    okogu: Mapped[str | None] = mapped_column(String(20))
    okved: Mapped[str | None] = mapped_column(String(500))
    legal_address: Mapped[str | None] = mapped_column(Text)
    postal_address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(255))
    director_name: Mapped[str | None] = mapped_column(String(255))
    chief_accountant: Mapped[str | None] = mapped_column(String(255))
    registration_details: Mapped[str | None] = mapped_column(Text)
    tax_office: Mapped[str | None] = mapped_column(String(500))
    bank_name: Mapped[str | None] = mapped_column(String(500))
    bank_address: Mapped[str | None] = mapped_column(Text)
    settlement_account: Mapped[str | None] = mapped_column(String(30))
    correspondent_account: Mapped[str | None] = mapped_column(String(30))
    bik: Mapped[str | None] = mapped_column(String(9))
    requisites_status: Mapped[str] = mapped_column(String(30), default="draft", server_default="draft")
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Contract(Base):
    __tablename__ = "contracts"
    record_version: Mapped[int] = mapped_column(server_default="1")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    counterparty: Mapped[str | None] = mapped_column(String(500))
    counterparty_organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
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
