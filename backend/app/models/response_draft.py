from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ResponseDraft(Base):
    __tablename__ = "response_drafts"
    __table_args__ = (UniqueConstraint("project_id", "source_file_id", "source_excerpt_hash", name="uq_response_source_excerpt"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    organizer_session_id: Mapped[int | None] = mapped_column(ForeignKey("organizer_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    source_file_id: Mapped[str] = mapped_column(String(255), index=True)
    source_file_name: Mapped[str] = mapped_column(String(1000))
    source_excerpt: Mapped[str] = mapped_column(Text)
    source_excerpt_hash: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
