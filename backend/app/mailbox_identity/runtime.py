"""Fail-closed mailbox runtime resolution; never guesses from message project."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.models.ai_secretary import Message
from app.models.mailbox_identity import MailboxCredentialGeneration, MailboxCutoverFlags
from app.models.v54_pilot import ConnectionIdentity, MailConnection, SourceCurrent, SourceReference, SourceVersion


@dataclass(frozen=True)
class MailboxRuntime:
    organization_id: int
    identity_id: str
    mail_connection_id: str
    generation: int
    binding_epoch: int
    google_token_id: int
    flags: MailboxCutoverFlags


def _runtime(db, generation):
    if not generation or generation.state != "active" or not generation.google_token_id:
        return None
    identity = db.get(ConnectionIdentity, generation.connection_identity_id)
    if (not identity or identity.state != "verified" or identity.binding_epoch != generation.binding_epoch
            or identity.credential_generation != generation.generation):
        return None
    mail = db.scalar(select(MailConnection).where(
        MailConnection.organization_id == generation.organization_id,
        MailConnection.identity_id == identity.id, MailConnection.namespace == "gmail",
        MailConnection.state == "active"))
    if not mail: return None
    flags = db.scalar(select(MailboxCutoverFlags).where(
        MailboxCutoverFlags.organization_id == generation.organization_id,
        MailboxCutoverFlags.mail_connection_id == mail.id,
        MailboxCutoverFlags.credential_generation == generation.generation))
    if not flags: return None
    return MailboxRuntime(generation.organization_id, identity.id, mail.id,
                          generation.generation, generation.binding_epoch,
                          generation.google_token_id, flags)


def runtime_for_project_connection(db, project_id):
    """OAuth bootstrap seam only; never used to recover an existing Message origin."""
    from app.models.google_token import GoogleOAuthToken
    token = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project_id))
    if not token: return None
    generation = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.google_token_id == token.id).order_by(
        MailboxCredentialGeneration.generation.desc()))
    return _runtime(db, generation)


def runtime_for_message(db, message: Message, *, action=False):
    if not message.mail_connection_id:
        # Reconciled-but-unresolved messages are forbidden from project fallback.
        if message.origin_version > 1: raise ValueError("resource_unavailable")
        return None
    mail = db.get(MailConnection, message.mail_connection_id)
    if not mail or mail.organization_id != message.organization_id: raise ValueError("resource_unavailable")
    generation = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.organization_id == message.organization_id,
        MailboxCredentialGeneration.connection_identity_id == mail.identity_id,
    ).order_by(MailboxCredentialGeneration.generation.desc()))
    runtime = _runtime(db, generation)
    if (not runtime or runtime.mail_connection_id != mail.id or not runtime.flags.primary_read
            or (action and not runtime.flags.actions)):
        raise ValueError("resource_unavailable")
    return runtime


def observe_gmail_message(db, *, runtime: MailboxRuntime, project_id: int,
                          provider_message_id: str, observation_key: str):
    source = db.scalar(select(SourceReference).where(
        SourceReference.organization_id == runtime.organization_id,
        SourceReference.identity_id == runtime.identity_id,
        SourceReference.namespace == "gmail", SourceReference.external_id == provider_message_id,
        SourceReference.incarnation == 1))
    if source is None:
        source = SourceReference(organization_id=runtime.organization_id, origin_project_id=project_id,
            identity_id=runtime.identity_id, namespace="gmail", external_id=provider_message_id,
            external_id_kind="provider_message_id", incarnation=1, object_kind="message",
            canonical_locator={"kind": "gmail_message"}, record_version=1,
            freshness="fresh", sync_state="observed", availability="available",
            last_seen_at=datetime.now(timezone.utc))
        db.add(source); db.flush()
    version = db.scalar(select(SourceVersion).where(
        SourceVersion.source_id == source.id, SourceVersion.observation_key == observation_key))
    if version is None:
        version = SourceVersion(organization_id=runtime.organization_id, source_id=source.id,
            revision=1, observation_key=observation_key, consistency="metadata_only",
            locator_at_observation={"kind": "gmail_message"}, integrity=[],
            observed_at=datetime.now(timezone.utc))
        db.add(version); db.flush()
    current = db.get(SourceCurrent, source.id)
    if current is None:
        db.add(SourceCurrent(source_id=source.id, organization_id=runtime.organization_id,
                             version_id=version.id))
    elif current.version_id != version.id:
        current.version_id = version.id
    db.flush()
    return source, version
