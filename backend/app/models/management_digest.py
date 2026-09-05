"""Persistence models for safe management digests and proposal origins."""

from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Time,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ManagementDigestPreference(Base):
    __tablename__ = "management_digest_preferences"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_management_digest_preference_scope"),
        CheckConstraint("record_version > 0", name="ck_management_digest_preference_version"),
        CheckConstraint("channel IN ('in_app','disabled')", name="ck_management_digest_preference_channel"),
        CheckConstraint("cadence IN ('daily','weekdays')", name="ck_management_digest_preference_cadence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="Europe/Moscow")
    quiet_start: Mapped[time] = mapped_column(Time, nullable=False, default=time(20, 0))
    quiet_end: Mapped[time] = mapped_column(Time, nullable=False, default=time(8, 0))
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="in_app")
    cadence: Mapped[str] = mapped_column(String(20), nullable=False, default="daily")
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class ManagementProposalOrigin(Base):
    """Append-only link from an analyzed origin to its exact proposal evidence."""

    __tablename__ = "management_proposal_origins"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "origin_type", "origin_id", "entity_type", "entity_id",
            name="uq_management_proposal_origin_target",
        ),
        CheckConstraint("origin_type IN ('meeting','message')", name="ck_management_proposal_origin_type"),
        CheckConstraint("entity_type IN ('obligation','decision')", name="ck_management_proposal_entity_type"),
        CheckConstraint("proposal_kind IN ('obligation','task','decision')", name="ck_management_proposal_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    origin_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    origin_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    proposal_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_pins: Mapped[list] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _deny_proposal_origin_mutation(_mapper, _connection, _target):
    raise ValueError("management_proposal_origin_is_append_only")


event.listen(ManagementProposalOrigin, "before_update", _deny_proposal_origin_mutation)
event.listen(ManagementProposalOrigin, "before_delete", _deny_proposal_origin_mutation)
