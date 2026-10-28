from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime, ForeignKey, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True,
    )
    cash_flow_entry_id: Mapped[int] = mapped_column(
        ForeignKey("cash_flow_entries.id", ondelete="CASCADE"), index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    purchase_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_items.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    match_status: Mapped[str] = mapped_column(
        String(20), default="none", server_default="none",
    )
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
