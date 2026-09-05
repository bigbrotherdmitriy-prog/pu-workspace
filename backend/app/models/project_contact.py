from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint, Uuid, event, func, text,
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
            "uq_project_contact_mailbox_project_email", "organization_id", "project_id",
            "mail_connection_id", "normalized_email", unique=True,
            postgresql_where=text("mail_connection_id IS NOT NULL"),
            sqlite_where=text("mail_connection_id IS NOT NULL"),
        ),
        CheckConstraint("record_version > 0", name="ck_project_contact_record_version"),
        CheckConstraint(
            "resolution_state IN ('proposed','conflict','confirmed','corrected','rejected')",
            name="ck_project_contact_resolution_state",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
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
    record_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    resolution_state: Mapped[str] = mapped_column(String(20), default="proposed", server_default="proposed", index=True)
    resolution_reason_code: Mapped[str] = mapped_column(String(50), default="manual", server_default="manual")
    company_activity: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectContactHistory(Base):
    """Append-only, PII-minimized history of human resolution decisions."""

    __tablename__ = "project_contact_history"
    __table_args__ = (
        UniqueConstraint("contact_id", "sequence", name="uq_project_contact_history_sequence"),
        UniqueConstraint("organization_id", "decision_key", name="uq_project_contact_history_decision"),
        CheckConstraint("sequence > 0 AND resulting_version > 1", name="ck_project_contact_history_versions"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("project_contacts.id", ondelete="RESTRICT"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    mail_connection_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    decision_key: Mapped[str] = mapped_column(String(100))
    command_hash: Mapped[str] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(20))
    from_state: Mapped[str] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    resulting_version: Mapped[int] = mapped_column(Integer)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    reason_code: Mapped[str] = mapped_column(String(50))
    changed_fields: Mapped[list] = mapped_column(JSON)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _deny_history_mutation(*_args, **_kwargs):
    raise ValueError("append_only_record")


event.listen(ProjectContactHistory, "before_update", _deny_history_mutation)
event.listen(ProjectContactHistory, "before_delete", _deny_history_mutation)
