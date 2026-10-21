from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IdRequirement(Base):
    __tablename__ = "id_requirements"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    object_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    section: Mapped[str | None] = mapped_column(String(60), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    doc_kind: Mapped[str] = mapped_column(String(30))
    copies: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_if_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20))
    norm_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdSource(Base):
    __tablename__ = "id_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", "folder_id", name="uq_id_source_folder"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True,
    )
    object_name: Mapped[str] = mapped_column(String(300))
    provider: Mapped[str] = mapped_column(String(30), default="google_drive")
    folder_id: Mapped[str] = mapped_column(String(500))
    folder_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_report: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdParticipant(Base):
    __tablename__ = "id_participants"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    object_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    role: Mapped[str] = mapped_column(String(30))
    position: Mapped[str | None] = mapped_column(String(300), nullable=True)
    full_name: Mapped[str] = mapped_column(String(300))
    order_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    nrs_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    org_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    org_ogrn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    org_inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    org_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdFile(Base):
    __tablename__ = "id_files"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", "external_id", name="uq_id_file_external"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(300), index=True)
    path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    section: Mapped[str | None] = mapped_column(String(60), nullable=True)
    object_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    issues: Mapped[list | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"), nullable=True,
    )
    contained_kinds: Mapped[list | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"), nullable=True,
    )
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("id_requirements.id", ondelete="SET NULL"), nullable=True,
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("id_sources.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
