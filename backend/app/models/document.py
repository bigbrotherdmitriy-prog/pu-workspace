from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "uq_documents_local_upload_identity",
            "project_id", "external_id", unique=True,
            postgresql_where=text("source = 'local_upload' AND external_id IS NOT NULL"),
            sqlite_where=text("source = 'local_upload' AND external_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    mime_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    parent_external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="google_drive",
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="discovered",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version: Mapped[int] = mapped_column(default=1)
    extraction_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    extraction_quality: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    ocr_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_review_status: Mapped[str] = mapped_column(String(30), default="not_required", index=True)
    ocr_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
