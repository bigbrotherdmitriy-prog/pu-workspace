from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("source_type", "source_external_id", name="uq_message_source"),
        UniqueConstraint("organization_id", "id", name="uq_v54_message_scope"),
        UniqueConstraint("mail_connection_id", "provider_message_id", name="uq_v54_message_mailbox"),
        ForeignKeyConstraint(["organization_id", "mail_connection_id"],
                             ["v54_mail_connections.organization_id", "v54_mail_connections.id"],
                             ondelete="RESTRICT", name="fk_v54_message_mail"),
        ForeignKeyConstraint(["organization_id", "source_reference_id"],
                             ["v54_sources.organization_id", "v54_sources.id"],
                             ondelete="RESTRICT", name="fk_v54_message_source"),
        CheckConstraint("context_version > 0", name="ck_v54_message_context_version"),
        CheckConstraint("(mail_connection_id IS NULL AND provider_message_id IS NULL AND source_reference_id IS NULL) OR "
                        "(mail_connection_id IS NOT NULL AND provider_message_id IS NOT NULL AND source_reference_id IS NOT NULL)",
                        name="ck_v54_message_origin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Nullable bridge: no mailbox inferred for historical messages.
    mail_connection_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False))
    provider_message_id: Mapped[str | None] = mapped_column(String(500))
    source_reference_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False))
    context_version: Mapped[int] = mapped_column(server_default="1")
    analysis_required: Mapped[bool] = mapped_column(server_default=text("false"))
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    source_external_id: Mapped[str] = mapped_column(String(500))
    source_name: Mapped[str] = mapped_column(String(1000))
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    source_sender: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_thread_id: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    attachments_json: Mapped[str] = mapped_column(Text, default="[]")
    summary: Mapped[str] = mapped_column(Text)
    context_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    context_evidence: Mapped[str] = mapped_column(Text)
    context_confirmed: Mapped[bool] = mapped_column(default=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="needs_review", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
