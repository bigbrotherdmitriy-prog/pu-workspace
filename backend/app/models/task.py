from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "source_file_id", "source_excerpt_hash", name="uq_task_source_excerpt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    assignee_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    organizer_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizer_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="assigned", index=True)
    priority: Mapped[str] = mapped_column(String(30), default="normal")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(50), default="document_analysis")
    source_file_id: Mapped[str] = mapped_column(String(255), index=True)
    source_file_name: Mapped[str] = mapped_column(String(1000))
    source_excerpt: Mapped[str] = mapped_column(Text)
    source_excerpt_hash: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(default=True)
    google_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    google_task_list_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    google_calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    google_calendar_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_calendar_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TaskDueDateHistory(Base):
    __tablename__ = "task_due_date_history"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    old_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    new_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    changed_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
