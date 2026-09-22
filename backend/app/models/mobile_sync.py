"""Durable receipts and user-visible conflicts for Android/PWA offline work."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MobileSyncCommand(Base):
    __tablename__ = "mobile_sync_commands"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", "client_mutation_id",
            name="uq_mobile_sync_command_identity",
        ),
        CheckConstraint(
            "status IN ('processing','applied','conflict','rejected')",
            name="ck_mobile_sync_commands_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    client_mutation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    device_id: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    base_record_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="processing")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )


class MobileSyncConflict(Base):
    __tablename__ = "mobile_sync_conflicts"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_mobile_sync_conflict_command"),
        CheckConstraint(
            "status IN ('unresolved','resolved')",
            name="ck_mobile_sync_conflicts_status",
        ),
        CheckConstraint(
            "resolution IS NULL OR resolution IN "
            "('keep_server','apply_local','latest_write_wins')",
            name="ck_mobile_sync_conflicts_resolution",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command_id: Mapped[int] = mapped_column(
        ForeignKey("mobile_sync_commands.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    base_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    local_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    server_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    conflicting_fields: Mapped[list] = mapped_column(JSON, nullable=False)
    server_record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    server_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unresolved")
    resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    resolved_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
