"""Read existing verified identity history; never infer identity from a token ID."""
from datetime import timezone
from hashlib import sha256

from sqlalchemy import select

from app.models.mailbox_identity import MailboxCredentialGeneration
from app.models.v54_pilot import ConnectionIdentity
from app.provider_actions.contracts import ProviderActionError


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def verified_rotation_pins(db, action, token, *, lock=False):
    """Token and identity generations are independent counters.

    The original action has no subject field. Prove continuity from append-only
    bindings predating it and every explicit reconnect since then. Missing or
    ambiguous history is not repaired using today's account.
    """
    if action is None or token is None:
        raise ProviderActionError("authority_stale")
    if token.credential_generation == action.credential_generation:
        return None
    if token.credential_generation < action.credential_generation:
        raise ProviderActionError("authority_stale")
    history = list(db.scalars(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.organization_id == action.organization_id,
        MailboxCredentialGeneration.google_token_id == token.id,
    ).order_by(MailboxCredentialGeneration.verified_at,
               MailboxCredentialGeneration.id).execution_options(populate_existing=True)))
    before = [item for item in history if _utc(item.verified_at) <= _utc(action.created_at)]
    after = [item for item in history if _utc(item.verified_at) > _utc(action.created_at)]
    if not before or len(after) != token.credential_generation - action.credential_generation:
        raise ProviderActionError("authority_stale")
    original = before[-1]
    # Equal timestamps may not hide a different account or binding epoch.
    relevant = [item for item in before if item.verified_at == original.verified_at] + after
    if any(item.state != "active" or
           (item.connection_identity_id, item.binding_epoch) !=
           (original.connection_identity_id, original.binding_epoch) for item in relevant):
        raise ProviderActionError("authority_stale")
    query = select(ConnectionIdentity).where(
        ConnectionIdentity.id == original.connection_identity_id,
        ConnectionIdentity.organization_id == action.organization_id,
    ).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    identity = db.scalar(query)
    latest = after[-1]
    if (identity is None or identity.state != "verified" or identity.provider != "google_workspace"
            or not identity.account_key or identity.binding_epoch != original.binding_epoch
            or identity.credential_generation != latest.generation
            or len({item.generation for item in relevant}) != len(relevant)):
        raise ProviderActionError("authority_stale")
    return (identity.id, identity.binding_epoch, identity.record_version,
            identity.credential_generation, sha256(identity.account_key.encode()).hexdigest(),
            tuple(item.id for item in relevant))
