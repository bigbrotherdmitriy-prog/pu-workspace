from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint, event, inspect
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthorityState(Base):
    """Current, explicit authority mandate for the inactive v5.4 pilot.

    The row is deliberately separate from legacy RBAC.  A user mandate is valid
    only while its recorded membership role still matches ProjectMember.
    """

    __tablename__ = "v54_authority_states"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
            name="fk_v54_authority_project",
        ),
        ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "organization_id", "project_id", "principal_kind", "principal_id", "scope",
            name="uq_v54_authority_principal_scope",
        ),
        CheckConstraint("principal_kind IN ('user','service')", name="ck_v54_authority_principal_kind"),
        CheckConstraint("state IN ('active','revoked')", name="ck_v54_authority_state"),
        CheckConstraint("authority_epoch > 0 AND record_version > 0", name="ck_v54_authority_versions"),
        Index("ix_v54_authority_lookup", "organization_id", "project_id", "principal_kind", "principal_id", "scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    principal_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    membership_role: Mapped[str | None] = mapped_column(String(50))
    permissions: Mapped[list] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    authority_epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer)


@event.listens_for(AuthorityState, "before_update")
def _authority_epoch_guard(mapper, connection, target):
    state = inspect(target)
    immutable = {"organization_id", "project_id", "principal_kind", "principal_id", "scope"}
    if any(state.attrs[name].history.has_changes() for name in immutable):
        raise ValueError("authority_scope_immutable")
    protected = {"membership_role", "permissions", "state", "valid_until"}
    if not any(state.attrs[name].history.has_changes() for name in protected):
        return
    epoch = state.attrs.authority_epoch.history
    version = state.attrs.record_version.history
    if (not epoch.deleted or not epoch.added or epoch.added[0] != epoch.deleted[0] + 1
            or not version.deleted or not version.added or version.added[0] != version.deleted[0] + 1
            or not state.attrs.updated_at.history.has_changes()):
        raise ValueError("authority_epoch_required")
