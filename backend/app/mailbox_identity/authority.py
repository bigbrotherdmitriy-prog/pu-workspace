"""Explicit owner renewal; narrows an existing grant, never bootstraps rights."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.mailbox_identity.dto import MailboxAuthorityRenewal
from app.mailbox_identity.service import MailboxConflict, _trusted_actor
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import MailboxAuthorityState, MailboxCredentialGeneration
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.v54_pilot import ConnectionIdentity, MailConnection


def renew_project_mailbox_authority(db, command, *, actor, expected_version, now=None):
    """Flush in caller transaction. Expired active grants may be renewed by owner.

    Prior grant + verified current credential ownership + live owner membership
    are the bootstrap boundary; an expired grant alone is never authorization.
    """
    command = MailboxAuthorityRenewal.model_validate(command)
    _trusted_actor(db, actor)
    now = now or datetime.now(timezone.utc)
    until = command.valid_until.astimezone(timezone.utc)
    if (type(expected_version) is not int or expected_version <= 0
            or not now < until <= now + timedelta(hours=24)):
        raise MailboxConflict("resource_unavailable")
    mail_id = str(command.mail_connection_id)
    mail_hint = db.get(MailConnection, mail_id)
    generation = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.organization_id == command.organization_id,
        MailboxCredentialGeneration.connection_identity_id == (mail_hint.identity_id if mail_hint else None),
        MailboxCredentialGeneration.generation == command.credential_generation,
        MailboxCredentialGeneration.binding_epoch == command.binding_epoch,
        MailboxCredentialGeneration.state == "active",
    ).with_for_update().execution_options(populate_existing=True))
    identity = db.scalar(select(ConnectionIdentity).where(
        ConnectionIdentity.id == (generation.connection_identity_id if generation else None),
    ).with_for_update().execution_options(populate_existing=True))
    mail = db.scalar(select(MailConnection).where(MailConnection.id == mail_id)
                     .with_for_update().execution_options(populate_existing=True))
    project = db.get(Project, command.project_id)
    role = db.scalar(select(ProjectMember.role).where(
        ProjectMember.project_id == command.project_id, ProjectMember.user_id == actor.id))
    token = db.get(GoogleOAuthToken, generation.google_token_id) if generation else None
    if (not project or project.organization_id != command.organization_id
            or project.archived_at is not None or role != "owner"
            or not generation or not token or token.project_id != command.project_id
            or not mail or mail.organization_id != command.organization_id
            or mail.state != "active" or mail.namespace != "gmail"
            or not identity or identity.organization_id != command.organization_id
            or mail.identity_id != identity.id or identity.state != "verified"
            or identity.credential_generation != command.credential_generation
            or identity.binding_epoch != command.binding_epoch):
        raise MailboxConflict("resource_unavailable")
    row = db.scalar(select(MailboxAuthorityState).where(
        MailboxAuthorityState.organization_id == command.organization_id,
        MailboxAuthorityState.mail_connection_id == mail_id,
        MailboxAuthorityState.principal_kind == "user",
        MailboxAuthorityState.principal_id == str(actor.id),
    ).with_for_update().execution_options(populate_existing=True))
    if (not row or row.authority_version != expected_version or row.state != "active"
            or not isinstance(row.permissions, list) or not row.permissions
            or any(p not in {"ingest", "read", "rollout"} for p in row.permissions)
            or "rollout" not in row.permissions
            or (row.scope_project_id is not None and row.scope_project_id != command.project_id)
            or ((row.scope_project_id is None) != (row.scope_credential_generation is None))):
        raise MailboxConflict("resource_unavailable")
    old_until = row.valid_until
    if old_until is None:
        raise MailboxConflict("resource_unavailable")
    old_until = old_until.replace(tzinfo=timezone.utc) if old_until.tzinfo is None else old_until
    if until <= old_until:
        raise MailboxConflict("resource_unavailable")
    old_scope = (row.scope_project_id, row.scope_credential_generation)
    result = db.execute(update(MailboxAuthorityState).where(
        MailboxAuthorityState.id == row.id,
        MailboxAuthorityState.authority_version == expected_version,
        MailboxAuthorityState.state == "active",
    ).values(valid_until=until, authority_version=expected_version + 1,
             scope_project_id=command.project_id,
             scope_credential_generation=command.credential_generation)
       .execution_options(synchronize_session="fetch"))
    if result.rowcount != 1:
        raise MailboxConflict("authority_version_conflict")
    db.add(AuditLog(action="mailbox_authority_renewed", entity_type="mailbox_authority",
        entity_id=row.id, details=(
            f"actor_user_id={actor.id};project_id={command.project_id};"
            f"generation={command.credential_generation};old_scope={old_scope};"
            f"from_version={expected_version};to_version={expected_version + 1};"
            f"old_valid_until={old_until.isoformat()};valid_until={until.isoformat()};"
            f"reason={command.reason};approval=CONFIRM;permissions_unchanged=true")))
    db.flush()
    return {"authority_version": expected_version + 1, "project_id": command.project_id,
            "credential_generation": command.credential_generation, "valid_until": until}
