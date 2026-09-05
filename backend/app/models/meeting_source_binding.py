"""Human-declared, immutable relation to an existing exact source observation."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, UniqueConstraint, Uuid, event, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MeetingSourceBinding(Base):
    __tablename__ = "meeting_source_bindings"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"], name="fk_meeting_binding_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id", "meeting_id"],
            ["meetings.project_id", "meetings.id"], name="fk_meeting_binding_meeting", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "source_id", "source_version_id"],
            ["v54_source_versions.organization_id", "v54_source_versions.source_id", "v54_source_versions.id"],
            name="fk_meeting_binding_observation", ondelete="RESTRICT"),
        UniqueConstraint("project_id", "meeting_id", "id", name="uq_meeting_binding_origin"),
        UniqueConstraint("meeting_id", "meeting_record_version", name="uq_meeting_binding_version"),
        UniqueConstraint("meeting_id", "command_id", name="uq_meeting_binding_command"),
        CheckConstraint("meeting_record_version > 1", name="ck_meeting_binding_version"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[int] = mapped_column(Integer)
    meeting_id: Mapped[int] = mapped_column(Integer, index=True)
    meeting_record_version: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    source_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    command_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    bound_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _immutable(_mapper, _connection, _target):
    raise ValueError("meeting_source_binding_is_append_only")


event.listen(MeetingSourceBinding, "before_update", _immutable)
event.listen(MeetingSourceBinding, "before_delete", _immutable)
