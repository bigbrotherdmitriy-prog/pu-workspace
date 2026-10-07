"""HTTP adapters for ADR-V6-07-REOPENING-RU condition 1 and ordinary mandate
upkeep.

`bootstrap` is the admin-only escape hatch: `AuthorityResolver.change()`
requires an already-active `authority.manage` mandate to extend one -- a
closed loop once the sole pilot authority row for a project expires or was
never created by a reproducible path. Bootstrap is gated by `require_admin`
(a *global* admin flag on the user), never by the pilot's own
`authority.manage` permission, so it works even when every mandate for the
project has lapsed. It still never touches an active, unexpired row.

`change` is the ordinary path for everything else (e.g. granting a wider
permission set to an already-active, unexpired mandate) -- it is gated only
by `require_user`, because `AuthorityResolver.change()` re-derives its own
authorization from the caller's live `authority.manage` mandate; no separate
global-admin check is layered on top of it here.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_admin, require_user
from app.core.observability import request_id_context
from app.core.v54_authority import AuthorityDenied, AuthorityResolver
from app.core.v54_interfaces import RequestScope
from app.core.v54_refs import ObjectRef, TaggedId
from app.database import get_db
from app.models.project import Project
from app.models.user import User


router = APIRouter(prefix="/api/v54/projects", tags=["v54-authority-admin"])


class AuthorityBootstrapRequest(BaseModel):
    principal_kind: Literal["user", "service"]
    principal_id: str = Field(min_length=1, max_length=100)
    membership_role: str | None = None
    permissions: list[str] = Field(min_length=1)


class AuthorityChangeRequest(BaseModel):
    principal_id: int = Field(gt=0)
    membership_role: str = Field(min_length=1, max_length=50)
    permissions: list[str] = Field(min_length=1)
    state: Literal["active", "revoked"]
    expected_epoch: int = Field(gt=0)


def _scope(db: Session, project_id: int, admin: User, request: Request) -> RequestScope:
    project = db.scalar(select(Project).where(
        Project.id == project_id, Project.archived_at.is_(None),
    ))
    if project is None:
        raise HTTPException(404, "resource_unavailable")
    tenant = TaggedId(kind="int", value=str(project.organization_id))
    return RequestScope(
        tenant=tenant,
        actor=ObjectRef(namespace="pu", type="user", tenant_id=tenant,
                        id=TaggedId(kind="int", value=str(admin.id))),
        project=ObjectRef(namespace="pu", type="project", tenant_id=tenant,
                          id=TaggedId(kind="int", value=str(project.id))),
        correlation_id=request_id_context.get() or "authority-bootstrap",
    )


@router.post("/{project_id}/authority/bootstrap")
def bootstrap_authority(project_id: int, command: AuthorityBootstrapRequest, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    scope = _scope(db, project_id, admin, request)
    try:
        epoch = AuthorityResolver().bootstrap(
            db, scope=scope, principal_kind=command.principal_kind,
            principal_id=command.principal_id, membership_role=command.membership_role,
            permissions=command.permissions,
        )
        db.commit()
    except (AuthorityDenied, ValueError) as error:
        db.rollback()
        raise HTTPException(409, "resource_unavailable") from error
    return {"authority_epoch": epoch}


@router.patch("/{project_id}/authority")
def change_authority(project_id: int, command: AuthorityChangeRequest, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(require_user)):
    scope = _scope(db, project_id, user, request)
    try:
        epoch = AuthorityResolver().change(
            db, scope=scope, principal_id=command.principal_id,
            membership_role=command.membership_role, permissions=command.permissions,
            state=command.state, expected_epoch=command.expected_epoch,
        )
        db.commit()
    except (AuthorityDenied, ValueError) as error:
        db.rollback()
        raise HTTPException(409, "resource_unavailable") from error
    return {"authority_epoch": epoch}
