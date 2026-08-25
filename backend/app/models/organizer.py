from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OrganizerSession(Base):
    __tablename__ = "organizer_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_folder_id: Mapped[str] = mapped_column(String(255))
    source_folder_name: Mapped[str] = mapped_column(String(500))
    copy_folder_id: Mapped[str | None] = mapped_column(String(255))
    copy_folder_name: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(50), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    source_item_count: Mapped[int | None] = mapped_column(Integer)
    copy_item_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrganizerProposal(Base):
    __tablename__ = "organizer_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("organizer_sessions.id", ondelete="CASCADE"), unique=True)
    folder_name: Mapped[str] = mapped_column(String(500))
    source_folder_id: Mapped[str] = mapped_column(String(255))
    copy_folder_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="waiting_confirmation", index=True)
    note: Mapped[str | None] = mapped_column(Text)
    originals_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizerAction(Base):
    __tablename__ = "organizer_actions"
    __table_args__ = (UniqueConstraint("proposal_id", "file_id", name="uq_organizer_action_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("organizer_proposals.id", ondelete="CASCADE"), index=True)
    action_order: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(500))
    target_folder: Mapped[str] = mapped_column(String(500))
    proposed_name: Mapped[str] = mapped_column(String(500))
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    file_id: Mapped[str] = mapped_column(String(255))
    current_parent_id: Mapped[str] = mapped_column(String(255))
    special_case: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    user_decision: Mapped[str] = mapped_column(String(50), default="pending")
    edited_name: Mapped[str | None] = mapped_column(String(500))
    edited_folder: Mapped[str | None] = mapped_column(String(500))


class OrganizerOperation(Base):
    __tablename__ = "organizer_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("organizer_proposals.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("organizer_sessions.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(String(255))
    op_type: Mapped[str] = mapped_column(String(50))
    before_json: Mapped[dict] = mapped_column(JSON)
    after_json: Mapped[dict] = mapped_column(JSON)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizerRule(Base):
    __tablename__ = "organizer_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern_json: Mapped[dict] = mapped_column(JSON)
    action_json: Mapped[dict] = mapped_column(JSON)
    exception_json: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(50))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
