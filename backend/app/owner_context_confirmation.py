"""Explicit owner confirmation for product AUTO context.

Model confidence and the legacy ``Message.context_confirmed`` routing flag are
not human authorization.  This module owns the separate, version-pinned proof
that the current project owner explicitly confirmed the exact message context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.v54_authority import AuthorityDenied, AuthorityResolver, PILOT_SCOPE
from app.core.v54_interfaces import RequestScope
from app.core.v54_refs import ObjectRef, TaggedId
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.v54_authority import AuthorityState


class OwnerContextConfirmationDenied(ValueError):
    """Stable, content-free rejection at the AUTO confirmation boundary."""


@dataclass(frozen=True, slots=True)
class OwnerContextConfirmationResult:
    message: Message
    state: str
    already_confirmed: bool


def _deny(code: str = "resource_unavailable"):
    raise OwnerContextConfirmationDenied(code)


def _scope(project: Project, user_id: int, correlation_id: str) -> RequestScope:
    tenant = TaggedId(kind="int", value=str(project.organization_id))
    return RequestScope(
        tenant=tenant,
        actor=ObjectRef(
            namespace="pu", type="user", tenant_id=tenant,
            id=TaggedId(kind="int", value=str(user_id)),
        ),
        project=ObjectRef(
            namespace="pu", type="project", tenant_id=tenant,
            id=TaggedId(kind="int", value=str(project.id)),
        ),
        correlation_id=correlation_id,
    )


def clear_owner_context_confirmation(message: Message) -> None:
    message.context_confirmed_by_user_id = None
    message.context_confirmed_by_user_at = None
    message.context_confirmed_context_version = None
    message.context_confirmed_authority_epoch = None


def owner_context_confirmation_state(db, message: Message) -> str:
    values = (
        message.context_confirmed_by_user_id,
        message.context_confirmed_by_user_at,
        message.context_confirmed_context_version,
        message.context_confirmed_authority_epoch,
    )
    if not any(value is not None for value in values):
        return "not_confirmed"
    if any(value is None for value in values):
        return "invalid"
    if message.context_confirmed_context_version != message.context_version:
        return "stale_context"
    member = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == message.project_id,
        ProjectMember.user_id == message.context_confirmed_by_user_id,
    ))
    authority = db.scalar(select(AuthorityState).where(
        AuthorityState.organization_id == message.organization_id,
        AuthorityState.project_id == message.project_id,
        AuthorityState.principal_kind == "user",
        AuthorityState.principal_id == str(message.context_confirmed_by_user_id),
        AuthorityState.scope == PILOT_SCOPE,
    ))
    now = datetime.now(timezone.utc)
    valid_until = authority.valid_until if authority is not None else None
    if valid_until is not None and valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    if (member is None or member.role != "owner" or authority is None
            or authority.state != "active" or authority.membership_role != "owner"
            or authority.authority_epoch != message.context_confirmed_authority_epoch
            or valid_until is None or valid_until <= now
            or "context.confirm" not in (authority.permissions or [])):
        return "stale_authority"
    return "confirmed_current"


def require_current_owner_context_confirmation(db, message: Message) -> None:
    if owner_context_confirmation_state(db, message) != "confirmed_current":
        _deny("owner_context_confirmation_required")


def confirm_owner_context_for_auto(
    db,
    *,
    message_id: int,
    project_id: int,
    contract_id: int,
    expected_context_version: int,
    user_id: int,
    authority: AuthorityResolver,
    clock,
    correlation_id: str,
) -> OwnerContextConfirmationResult:
    if (type(message_id) is not int or message_id <= 0
            or type(project_id) is not int or project_id <= 0
            or type(contract_id) is not int or contract_id <= 0
            or type(expected_context_version) is not int or expected_context_version <= 0
            or type(user_id) is not int or user_id <= 0):
        _deny()
    now = clock()
    if now.tzinfo is None:
        _deny()
    project = db.scalar(select(Project).where(
        Project.id == project_id,
        Project.archived_at.is_(None),
    ))
    if project is None:
        _deny()
    scope = _scope(project, user_id, correlation_id)
    try:
        snapshot = authority.require(db, scope, "context.confirm", now, lock=True)
    except AuthorityDenied:
        _deny()
    if snapshot.membership_role != "owner":
        _deny()

    message = db.scalar(select(Message).where(
        Message.id == message_id,
        Message.organization_id == project.organization_id,
    ).with_for_update().execution_options(populate_existing=True))
    if (message is None or message.project_id != project_id
            or message.contract_id != contract_id
            or message.context_version != expected_context_version
            or not message.context_confirmed
            or message.context_confidence < 0.9
            or message.source_type != "email"
            or not message.mail_connection_id
            or not message.provider_message_id
            or not message.source_reference_id):
        _deny("context_version_conflict")
    contract = db.scalar(select(Contract.id).where(
        Contract.id == contract_id,
        Contract.project_id == project_id,
    ))
    if contract is None:
        _deny()

    if (message.context_confirmed_by_user_id == user_id
            and message.context_confirmed_context_version == message.context_version
            and message.context_confirmed_authority_epoch == snapshot.authority_epoch
            and message.context_confirmed_by_user_at is not None):
        return OwnerContextConfirmationResult(message, "confirmed_current", True)

    message.context_confirmed_by_user_id = user_id
    message.context_confirmed_by_user_at = now
    message.context_confirmed_context_version = message.context_version
    message.context_confirmed_authority_epoch = snapshot.authority_epoch
    db.add(AuditLog(
        action="message_auto_context_owner_confirmed",
        entity_type="message",
        entity_id=message.id,
        details=(f"project={project_id}; contract={contract_id}; "
                 f"context_version={message.context_version}; authority_epoch={snapshot.authority_epoch}"),
    ))
    db.flush()
    return OwnerContextConfirmationResult(message, "confirmed_current", False)
