from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, Uuid, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProjectContact(Base):
    __tablename__ = "project_contacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "mail_connection_id"],
            ["v54_mail_connections.organization_id", "v54_mail_connections.id"],
            ondelete="RESTRICT", name="fk_project_contact_mailbox_scope",
        ),
        Index(
            "uq_project_contact_legacy_email", "organization_id", "normalized_email",
            unique=True, postgresql_where=text("mail_connection_id IS NULL"),
            sqlite_where=text("mail_connection_id IS NULL"),
        ),
        Index(
            "uq_project_contact_mailbox_email", "organization_id", "mail_connection_id",
            "normalized_email", unique=True,
            postgresql_where=text("mail_connection_id IS NOT NULL"),
            sqlite_where=text("mail_connection_id IS NOT NULL"),
        ),
        CheckConstraint("record_version > 0", name="ck_project_contacts_record_version"),
        CheckConstraint(
            "resolution_state IN ('proposed','conflict','confirmed','corrected','rejected')",
            name="ck_project_contact_resolution_state",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mail_connection_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True, index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True)
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    company: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email: Mapped[str] = mapped_column(String(500))
    normalized_email: Mapped[str] = mapped_column(String(500), index=True)
    normalized_domain: Mapped[str | None] = mapped_column(String(253), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    normalized_phone: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    resolution_state: Mapped[str] = mapped_column(String(20), default="proposed", server_default="proposed", index=True)
    resolution_reason_code: Mapped[str] = mapped_column(String(50), default="manual", server_default="manual")
    company_activity: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ContactConflict(Base):
    __tablename__ = "contact_conflicts"
    __table_args__ = (CheckConstraint("record_version > 0", name="ck_contact_conflicts_record_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("project_contacts.id", ondelete="CASCADE"), index=True)
    current_project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    candidate_project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    normalized_email: Mapped[str] = mapped_column(String(500), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending", index=True)
    resolution: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
