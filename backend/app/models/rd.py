from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RdVolume(Base):
    __tablename__ = "rd_volumes"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", "external_id", name="uq_rd_volume_source"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    object_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    section: Mapped[str | None] = mapped_column(String(60), nullable=True)
    code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(500))
    provider: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="new")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spec_pages: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    items: Mapped[list["RdSpecItem"]] = relationship(
        back_populates="volume",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RdSpecItem(Base):
    __tablename__ = "rd_spec_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    volume_id: Mapped[int] = mapped_column(
        ForeignKey("rd_volumes.id", ondelete="CASCADE"), index=True,
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    pos: Mapped[str | None] = mapped_column(String(40), nullable=True)
    name: Mapped[str] = mapped_column(Text)
    type_mark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(300), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    volume: Mapped["RdVolume"] = relationship(back_populates="items")


class RdSheet(Base):
    __tablename__ = "rd_sheets"
    __table_args__ = (
        UniqueConstraint("volume_id", "page_no", name="uq_rd_sheet_page"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    volume_id: Mapped[int] = mapped_column(
        ForeignKey("rd_volumes.id", ondelete="CASCADE"), index=True,
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    sheet_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sheet_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    text_source: Mapped[str] = mapped_column(String(10))
    text_len: Mapped[int] = mapped_column(Integer, default=0)
