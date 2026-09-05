"""Owner-only HTTP adapter for the v5.4 autonomy policy service."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.autonomy_policy import (
    ActionCandidate, AutonomyConflict, AutonomyDenied, AutonomyPolicyService,
    PolicyAssignmentCommand, PolicyRevokeCommand,
)
from app.core.auth import require_user
from app.core.observability import request_id_context
from app.core.v54_authority import AuthorityResolver
from app.core.v54_interfaces import RequestScope
from app.core.v54_refs import ObjectRef, TaggedId
from app.database import get_db
from app.models.project import Project
from app.models.user import User


router = APIRouter(prefix="/api/v54/projects", tags=["v54-autonomy-policy"])
_NO_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def get_autonomy_clock():
    return lambda: datetime.now(timezone.utc)


def _scope(db: Session, project_id: int, user: User, request: Request) -> RequestScope:
    project = db.scalar(select(Project).where(
        Project.id == project_id, Project.archived_at.is_(None),
    ))
    if project is None:
        raise AutonomyDenied("resource_unavailable")
    tenant = TaggedId(kind="int", value=str(project.organization_id))
    return RequestScope(
        tenant=tenant,
        actor=ObjectRef(namespace="pu", type="user", tenant_id=tenant,
                        id=TaggedId(kind="int", value=str(user.id))),
        project=ObjectRef(namespace="pu", type="project", tenant_id=tenant,
                          id=TaggedId(kind="int", value=str(project.id))),
        correlation_id=request_id_context.get() or "autonomy-policy",
    )


def _service(clock):
    return AutonomyPolicyService(authority=AuthorityResolver(clock=clock), clock=clock)


def _handle(error: Exception):
    if isinstance(error, AutonomyConflict):
        raise HTTPException(409, "policy_conflict") from error
    raise HTTPException(404, "resource_unavailable") from error


@router.get("/{project_id}/autonomy-policy")
def get_policy(project_id: int, request: Request, response: Response,
               db: Session = Depends(get_db), user: User = Depends(require_user),
               clock=Depends(get_autonomy_clock)):
    try:
        result = _service(clock).get(db, scope=_scope(db, project_id, user, request))
    except (AutonomyDenied, AutonomyConflict, ValueError) as error:
        _handle(error)
    response.headers.update(_NO_CACHE)
    return result


@router.put("/{project_id}/autonomy-policy")
def assign_policy(project_id: int, command: PolicyAssignmentCommand, request: Request,
                  response: Response, db: Session = Depends(get_db),
                  user: User = Depends(require_user), clock=Depends(get_autonomy_clock)):
    try:
        result = _service(clock).assign(db, scope=_scope(db, project_id, user, request), command=command)
        db.commit()
    except (AutonomyDenied, AutonomyConflict, ValueError) as error:
        db.rollback()
        _handle(error)
    response.headers.update(_NO_CACHE)
    return result


@router.post("/{project_id}/autonomy-policy/revoke")
def revoke_policy(project_id: int, command: PolicyRevokeCommand, request: Request,
                  response: Response, db: Session = Depends(get_db),
                  user: User = Depends(require_user), clock=Depends(get_autonomy_clock)):
    try:
        result = _service(clock).revoke(db, scope=_scope(db, project_id, user, request), command=command)
        db.commit()
    except (AutonomyDenied, AutonomyConflict, ValueError) as error:
        db.rollback()
        _handle(error)
    response.headers.update(_NO_CACHE)
    return result


@router.post("/{project_id}/autonomy-policy/decide")
def decide(project_id: int, candidate: ActionCandidate, request: Request, response: Response,
           db: Session = Depends(get_db), user: User = Depends(require_user),
           clock=Depends(get_autonomy_clock)):
    try:
        result = _service(clock).decide(db, scope=_scope(db, project_id, user, request), candidate=candidate)
    except (AutonomyDenied, AutonomyConflict, ValueError) as error:
        _handle(error)
    response.headers.update(_NO_CACHE)
    return result
