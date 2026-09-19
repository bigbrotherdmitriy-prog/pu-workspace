from __future__ import annotations

import logging
import os

from sqlalchemy import select

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User

log = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv("NOTIFICATION_REFRESH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def interval_seconds() -> int:
    return max(60, int(os.getenv("NOTIFICATION_REFRESH_INTERVAL_SECONDS", "300")))


def refresh_project_members_once() -> dict[str, int]:
    """Refresh every active project member through the same domain path as the API."""
    from app.api.management import refresh_notifications_for_user

    with SessionLocal() as db:
        targets = list(db.execute(
            select(ProjectMember.project_id, ProjectMember.user_id)
            .join(Project, Project.id == ProjectMember.project_id)
            .where(Project.archived_at.is_(None))
            .order_by(ProjectMember.project_id, ProjectMember.user_id)
        ))

    totals = {"projects": len({project_id for project_id, _ in targets}),
              "members": 0, "failed": 0}
    for project_id, user_id in targets:
        with SessionLocal() as db:
            try:
                user = db.get(User, user_id)
                if user is None:
                    raise LookupError("notification refresh user is missing")
                refresh_notifications_for_user(project_id, db, user)
                totals["members"] += 1
            except Exception as exc:
                db.rollback()
                totals["failed"] += 1
                db.add(AuditLog(
                    action="notification_auto_refresh_failed",
                    entity_type="project",
                    entity_id=project_id,
                    details=f"error={exc.__class__.__name__}",
                ))
                db.commit()
                log.exception("Automatic notification refresh failed for project %s member %s",
                              project_id, user_id)
    return totals
