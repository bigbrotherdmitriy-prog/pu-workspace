from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (
        UniqueConstraint("project_id", "group_key", name="uq_purchase_item_group"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True,
    )
    group_key: Mapped[str] = mapped_column(String(600))
    name: Mapped[str] = mapped_column(Text)
    type_mark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(300), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    qty_needed: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), server_default="0",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="need", server_default="need",
    )
    qty_ordered: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), default=Decimal("0"), server_default="0",
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    sources: Mapped[list["PurchaseItemSource"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PurchaseItemSource(Base):
    __tablename__ = "purchase_item_sources"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_items.id", ondelete="CASCADE"), index=True,
    )
    spec_item_id: Mapped[int] = mapped_column(
        ForeignKey("rd_spec_items.id", ondelete="CASCADE"), unique=True,
    )
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)

    item: Mapped["PurchaseItem"] = relationship(back_populates="sources")
