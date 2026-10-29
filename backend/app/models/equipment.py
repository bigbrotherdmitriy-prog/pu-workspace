from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EquipmentUnit(Base):
    __tablename__ = "equipment_units"
    __table_args__ = (
        UniqueConstraint("project_id", "rd_spec_item_id", "unit_no", name="uq_equipment_unit"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    rd_spec_item_id: Mapped[int | None] = mapped_column(ForeignKey("rd_spec_items.id", ondelete="SET NULL"), nullable=True)
    purchase_item_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_items.id", ondelete="SET NULL"), nullable=True)
    unit_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    name: Mapped[str] = mapped_column(Text)
    type_mark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(300), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="planned", server_default="planned")
    received_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    installed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_interval_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_service_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
