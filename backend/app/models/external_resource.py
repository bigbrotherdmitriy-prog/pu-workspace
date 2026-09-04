from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExternalResourceLink(Base):
    """Provider-neutral link between a Core entity and an external resource."""

    __tablename__ = "external_resource_links"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "provider", "resource_type",
            name="uq_external_resource_entity_provider_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int] = mapped_column(index=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(80))
    external_id: Mapped[str] = mapped_column(String(500), index=True)
    container_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(40), default="synced", index=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
