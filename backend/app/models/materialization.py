"""Durable, deny-by-default lifecycle for encrypted materializations."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    JSON, String, UniqueConstraint, Uuid, event, inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.v54_pilot import new_id


class Materialization(Base):
    __tablename__ = "v54_materializations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_v54_materialization_scope"),
        UniqueConstraint("organization_id", "object_id", name="uq_v54_materialization_object"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"], ["projects.organization_id", "projects.id"],
            name="fk_v54_materialization_project", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            ["v54_source_versions.organization_id", "v54_source_versions.source_id",
             "v54_source_versions.id"],
            name="fk_v54_materialization_observation", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_id", "source_id", "source_version_id"],
            ["v54_evidence.organization_id", "v54_evidence.id", "v54_evidence.source_id",
             "v54_evidence.source_version_id"],
            name="fk_v54_materialization_evidence", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "parent_id"],
            ["v54_materializations.organization_id", "v54_materializations.id"],
            name="fk_v54_materialization_parent", ondelete="RESTRICT",
        ),
        CheckConstraint("record_version > 0", name="ck_v54_materialization_version"),
        CheckConstraint(
            "state IN ('ADMITTED','WRITING','SEALED','DERIVED','EXPIRED','PURGED')",
            name="ck_v54_materialization_state",
        ),
        CheckConstraint("length(object_id) = 32", name="ck_v54_materialization_object_id"),
        CheckConstraint("active_fence IS NULL OR length(active_fence) = 32",
                        name="ck_v54_materialization_fence"),
        CheckConstraint("chunk_size IS NULL OR chunk_size > 0",
                        name="ck_v54_materialization_chunk_size"),
        CheckConstraint("format_version IS NULL OR format_version > 0",
                        name="ck_v54_materialization_format"),
        CheckConstraint(
            "(state = 'ADMITTED' AND active_fence IS NULL AND format_version IS NULL "
            "AND chunk_size IS NULL AND wrapped_dek IS NULL AND manifest IS NULL "
            "AND writing_at IS NULL AND sealed_at IS NULL AND derived_at IS NULL "
            "AND expired_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'WRITING' AND active_fence IS NOT NULL AND writing_at IS NOT NULL "
            "AND format_version IS NULL AND chunk_size IS NULL AND wrapped_dek IS NULL "
            "AND manifest IS NULL AND sealed_at IS NULL AND derived_at IS NULL "
            "AND expired_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'SEALED' AND active_fence IS NOT NULL AND writing_at IS NOT NULL "
            "AND format_version IS NOT NULL AND chunk_size IS NOT NULL AND wrapped_dek IS NOT NULL "
            "AND manifest IS NOT NULL AND sealed_at IS NOT NULL AND derived_at IS NULL "
            "AND expired_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'DERIVED' AND active_fence IS NULL AND writing_at IS NOT NULL "
            "AND format_version IS NOT NULL AND chunk_size IS NOT NULL AND wrapped_dek IS NOT NULL "
            "AND manifest IS NOT NULL AND sealed_at IS NOT NULL AND derived_at IS NOT NULL "
            "AND expired_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'EXPIRED' AND active_fence IS NULL AND expired_at IS NOT NULL "
            "AND purged_at IS NULL) OR "
            "(state = 'PURGED' AND active_fence IS NULL AND format_version IS NULL "
            "AND chunk_size IS NULL AND wrapped_dek IS NULL AND manifest IS NOT NULL "
            "AND expired_at IS NOT NULL AND purged_at IS NOT NULL)",
            name="ck_v54_materialization_shape",
        ),
        Index("ix_v54_materialization_retention", "state", "retention_until"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    source_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    source_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    evidence_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    parent_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    object_id: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), server_default="ADMITTED")
    record_version: Mapped[int] = mapped_column(server_default="1")
    active_fence: Mapped[str | None] = mapped_column(String(32))
    kek_reference: Mapped[str] = mapped_column(String(255))
    kek_version: Mapped[str] = mapped_column(String(255))
    format_version: Mapped[int | None] = mapped_column()
    chunk_size: Mapped[int | None] = mapped_column()
    wrapped_dek: Mapped[str | None] = mapped_column(String(255))
    manifest: Mapped[dict | None] = mapped_column(JSON)
    residency: Mapped[str] = mapped_column(String(100))
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    copy_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                               server_default="false")
    derive_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                 server_default="false")
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    writing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    derived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_MUTABLE = {
    "state", "record_version", "active_fence", "format_version", "chunk_size",
    "wrapped_dek", "manifest", "writing_at", "sealed_at", "derived_at",
    "expired_at", "purged_at",
}


@event.listens_for(Materialization, "before_update")
def _protect_materialization(mapper, connection, target):
    changed = {a.key for a in inspect(target).attrs if a.history.has_changes()}
    if changed - _MUTABLE:
        raise ValueError("immutable_materialization_binding")


@event.listens_for(Materialization, "before_delete")
def _deny_materialization_delete(mapper, connection, target):
    raise ValueError("materialization_tombstone_required")
