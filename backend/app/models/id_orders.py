from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IdOrder(Base):
    __tablename__ = "id_orders"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    person_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    position: Mapped[str | None] = mapped_column(String(300), nullable=True)
    org_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    object_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signer_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    signer_position: Mapped[str | None] = mapped_column(String(300), nullable=True)
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdOrderFile(Base):
    __tablename__ = "id_order_files"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    order_id: Mapped[int] = mapped_column(ForeignKey("id_orders.id", ondelete="CASCADE"), index=True)
    file_kind: Mapped[str] = mapped_column(String(30), default="certificate", server_default="certificate")
    filename: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
