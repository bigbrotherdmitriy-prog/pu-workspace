from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SavedSearchView(Base):
    __tablename__ = "saved_search_views"
    __table_args__ = (
        CheckConstraint("record_version > 0", name="ck_saved_search_view_version"),
        CheckConstraint("state IN ('active','deleted')", name="ck_saved_search_view_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    filters: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="active", server_default="active", index=True)
    record_version: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SavedSearchViewHistory(Base):
    __tablename__ = "saved_search_view_history"
    __table_args__ = (
        UniqueConstraint("view_id", "sequence", name="uq_saved_search_view_history_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    view_id: Mapped[int] = mapped_column(ForeignKey("saved_search_views.id", ondelete="RESTRICT"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    sequence: Mapped[int] = mapped_column()
    event: Mapped[str] = mapped_column(String(30))
    resulting_version: Mapped[int] = mapped_column()
    snapshot: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _deny_history_mutation(mapper, connection, target):
    raise ValueError("saved_view_history_is_append_only")


event.listen(SavedSearchViewHistory, "before_update", _deny_history_mutation)
event.listen(SavedSearchViewHistory, "before_delete", _deny_history_mutation)
