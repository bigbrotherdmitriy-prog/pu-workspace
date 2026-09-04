from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ResponseDraft(Base):
    __tablename__ = "response_drafts"
    __table_args__ = (UniqueConstraint("project_id", "source_file_id", "source_excerpt_hash", name="uq_response_source_excerpt"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    organizer_session_id: Mapped[int | None] = mapped_column(ForeignKey("organizer_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    recipient_to: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    recipient_cc: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    recipient_bcc: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    attachments_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    provider: Mapped[str] = mapped_column(String(50), default="google_workspace", server_default="google_workspace", index=True)
    operation_kind: Mapped[str] = mapped_column(String(30), default="reply", server_default="reply")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    approved_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    source_file_id: Mapped[str] = mapped_column(String(255), index=True)
    source_file_name: Mapped[str] = mapped_column(String(1000))
    source_excerpt: Mapped[str] = mapped_column(Text)
    source_excerpt_hash: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    sent_external_id: Mapped[str | None] = mapped_column(String(500), nullable=True, unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_send_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
