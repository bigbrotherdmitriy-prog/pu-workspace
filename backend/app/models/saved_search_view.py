from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SavedSearchView(Base):
    """Private, versioned project-search filters owned by one user."""

    __tablename__ = "saved_search_views"
    __table_args__ = (
        ForeignKeyConstraint(
            ("organization_id", "project_id"),
            ("projects.organization_id", "projects.id"),
            name="fk_saved_search_views_project_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint("record_version > 0", name="ck_saved_search_views_record_version"),
        Index(
            "uq_saved_search_views_active_owner_name",
            "project_id",
            "owner_user_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
