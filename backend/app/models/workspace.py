from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SourceFolder(Base):
    __tablename__ = "source_folders"
    __table_args__ = (UniqueConstraint("project_id", "external_id", name="uq_source_folder_project_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500))
    provider: Mapped[str] = mapped_column(String(50), default="google_drive")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceSnapshot(Base):
    __tablename__ = "workspace_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_folder_id: Mapped[int] = mapped_column(ForeignKey("source_folders.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(50), default="building", index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    analysis_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    analysis_result: Mapped[dict | None] = mapped_column(JSON)
    analysis_error: Mapped[str | None] = mapped_column(Text)
    analysis_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VirtualNode(Base):
    __tablename__ = "virtual_nodes"
    __table_args__ = (UniqueConstraint("snapshot_id", "external_id", name="uq_virtual_node_snapshot_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("workspace_snapshots.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    parent_external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(255))
    node_type: Mapped[str] = mapped_column(String(20), index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(128))
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_path: Mapped[str | None] = mapped_column(Text)
    parent_external_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    provider_revision: Mapped[str | None] = mapped_column(String(500))
    provider_metadata_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    web_url: Mapped[str | None] = mapped_column(Text)
    availability: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    acl_state: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    analysis_state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    shortcut_target_id: Mapped[str | None] = mapped_column(String(255))
    shortcut_target_mime_type: Mapped[str | None] = mapped_column(String(255))
    shortcut_target_resource_key: Mapped[str | None] = mapped_column(String(500))


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    extractor: Mapped[str] = mapped_column(String(100))
    text_content: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
