from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkLogCrew(Base):
    __tablename__ = "work_log_crews"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    foreman: Mapped[str | None] = mapped_column(String(300), nullable=True)
    members: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkLogEntry(Base):
    __tablename__ = "work_log_entries"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    object_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    work_name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    weather: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    crew_id: Mapped[int | None] = mapped_column(ForeignKey("work_log_crews.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rd_spec_item_id: Mapped[int | None] = mapped_column(ForeignKey("rd_spec_items.id", ondelete="SET NULL"), nullable=True)
    schedule_item_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_items.id", ondelete="SET NULL"), nullable=True)
    estimate_line_id: Mapped[int | None] = mapped_column(ForeignKey("estimate_lines.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkLogLabor(Base):
    """Табель: кто и сколько часов отработал в день записи. Ставка хранится справочно, ни к чему не привязана."""
    __tablename__ = "work_log_labor"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("work_log_entries.id", ondelete="CASCADE"), index=True)
    person_name: Mapped[str] = mapped_column(String(300))
    hours: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
