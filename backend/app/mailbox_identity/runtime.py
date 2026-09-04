"""Fail-closed mailbox runtime resolution from append-only current origin."""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.models.ai_secretary import Message
from app.models.mailbox_identity import (
    MailboxAuthorityState, MailboxCredentialGeneration, MailboxCutoverFlags,
    MailboxOriginBinding, MailboxOriginCurrent, MailboxOriginDecision,
)
from app.models.user import User
from app.models.v54_pilot import (
    ConnectionIdentity, Evidence, EvidenceAssessment, MailConnection,
    SourceCurrent, SourceReference, SourceVersion,
)


@dataclass(frozen=True)
class MailboxRuntime:
    organization_id: int
    identity_id: str
    mail_connection_id: str
    generation: int
    binding_epoch: int
    google_token_id: int
    flags: MailboxCutoverFlags
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    source_reference_id: str | None = None
    source_version_id: str | None = None
    binding_id: str | None = None
    mailbox_cohort: bool = False


def _deny():
    raise ValueError("resource_unavailable")


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _runtime(db, generation, *, expected_mail_connection_id=None):
    if not generation or generation.state != "active" or not generation.google_token_id:
        return None
    identity = db.get(ConnectionIdentity, generation.connection_identity_id)
    if (not identity or identity.organization_id != generation.organization_id
            or identity.state != "verified" or identity.binding_epoch != generation.binding_epoch
            or identity.credential_generation != generation.generation):
        return None
    query = select(MailConnection).where(
        MailConnection.organization_id == generation.organization_id,
        MailConnection.identity_id == identity.id, MailConnection.namespace == "gmail",
        MailConnection.state == "active")
    if expected_mail_connection_id is not None:
        query = query.where(MailConnection.id == expected_mail_connection_id)
    mail = db.scalar(query)
    if not mail:
        return None
    flags = db.scalar(select(MailboxCutoverFlags).where(
        MailboxCutoverFlags.organization_id == generation.organization_id,
        MailboxCutoverFlags.mail_connection_id == mail.id,
        MailboxCutoverFlags.credential_generation == generation.generation))
    if not flags:
        return None
    mailbox_cohort = bool(db.scalar(select(MailboxCutoverFlags.id).where(
        MailboxCutoverFlags.organization_id == generation.organization_id,
        MailboxCutoverFlags.mail_connection_id == mail.id,
        or_(MailboxCutoverFlags.pilot_write.is_(True),
            MailboxCutoverFlags.primary_read.is_(True),
            MailboxCutoverFlags.actions.is_(True))).limit(1)))
    return MailboxRuntime(generation.organization_id, identity.id, mail.id,
                          generation.generation, generation.binding_epoch,
                          generation.google_token_id, flags, mailbox_cohort=mailbox_cohort)


def require_mailbox_authority(db, *, runtime, actor: User, permission: str, expected_version=None):
    """Actor is trusted server context and is intentionally absent from request DTOs."""
    if not actor or not actor.id or db.get(User, actor.id) is not actor:
        _deny()
    row = db.scalar(select(MailboxAuthorityState).where(
        MailboxAuthorityState.organization_id == runtime.organization_id,
        MailboxAuthorityState.mail_connection_id == runtime.mail_connection_id,
        MailboxAuthorityState.principal_kind == "user",
        MailboxAuthorityState.principal_id == str(actor.id),
    ).with_for_update())
    valid_until = _aware(row.valid_until) if row else None
    if (not row or row.state != "active" or not isinstance(row.permissions, list)
            or permission not in row.permissions or not valid_until
            or valid_until <= datetime.now(timezone.utc)
            or (expected_version is not None and row.authority_version != expected_version)):
        _deny()
    return row


def runtime_for_project_connection(db, project_id):
    """Resolve a project bootstrap token to the identity's exact current generation."""
    from app.models.google_token import GoogleOAuthToken
    token = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project_id))
    if not token:
        return None
    mapped = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.google_token_id == token.id).order_by(
        MailboxCredentialGeneration.generation.desc()))
    if not mapped:
        return None
    identity = db.get(ConnectionIdentity, mapped.connection_identity_id)
    if not identity or identity.organization_id != mapped.organization_id:
        _deny()
    generation = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.organization_id == mapped.organization_id,
        MailboxCredentialGeneration.connection_identity_id == identity.id,
        MailboxCredentialGeneration.generation == identity.credential_generation,
        MailboxCredentialGeneration.binding_epoch == identity.binding_epoch))
    runtime = _runtime(db, generation)
    if not runtime:
        _deny()
    return runtime


def provider_locator(source):
    locator = source.canonical_locator
    if not isinstance(locator, dict) or set(locator) - {
        "kind", "provider_message_id", "provider_thread_id",
    } or locator.get("kind") != "gmail_message":
        _deny()
    message_id = locator.get("provider_message_id")
    thread_id = locator.get("provider_thread_id")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 500:
        _deny()
    if thread_id is not None and (not isinstance(thread_id, str) or not thread_id or len(thread_id) > 500):
        _deny()
    return message_id, thread_id


def _evidence_is_current(db, decision, source, source_version):
    refs = decision.evidence_refs
    if decision.reason_code == "provider_observed_ingress":
        return refs == []
    if not isinstance(refs, list) or not refs:
        return False
    now = datetime.now(timezone.utc)
    seen = set()
    for pin in refs:
        if (not isinstance(pin, dict)
                or set(pin) != {"evidence_id", "evidence_revision", "assessment_record_version"}
                or pin.get("evidence_id") in seen):
            return False
        seen.add(pin.get("evidence_id"))
        evidence = db.get(Evidence, pin.get("evidence_id"))
        assessment = db.get(EvidenceAssessment, pin.get("evidence_id"))
        valid_until = _aware(assessment.valid_until) if assessment else None
        if (not evidence or evidence.organization_id != decision.organization_id
                or evidence.source_id != source.id or evidence.source_version_id != source_version.id
                or evidence.revision != pin.get("evidence_revision") or not assessment
                or assessment.record_version != pin.get("assessment_record_version")
                or assessment.verification != "verified" or assessment.freshness != "fresh"
                or assessment.availability != "available" or not assessment.reviewed_by
                or not valid_until or valid_until <= now):
            return False
    return True


def runtime_for_message(db, message: Message, *, actor: User, action=False):
    if not message.mail_connection_id:
        if message.origin_version > 1:
            _deny()
        return None
    current = db.scalar(select(MailboxOriginCurrent).where(
        MailboxOriginCurrent.organization_id == message.organization_id,
        MailboxOriginCurrent.message_id == message.id).with_for_update())
    binding = db.get(MailboxOriginBinding, current.binding_id) if current and current.binding_id else None
    decision = db.get(MailboxOriginDecision, binding.decision_id) if binding else None
    if (not binding or not decision or binding.state != "confirmed"
            or binding.organization_id != message.organization_id or binding.message_id != message.id
            or decision.organization_id != message.organization_id or decision.message_id != message.id
            or decision.outcome != "CONFIRM" or decision.source_reference_id != binding.source_reference_id
            or decision.binding_epoch != binding.binding_epoch
            or decision.credential_generation != binding.credential_generation):
        _deny()
    generation = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.organization_id == message.organization_id,
        MailboxCredentialGeneration.connection_identity_id == decision.identity_id,
        MailboxCredentialGeneration.generation == binding.credential_generation,
        MailboxCredentialGeneration.binding_epoch == binding.binding_epoch))
    runtime = _runtime(db, generation, expected_mail_connection_id=binding.mail_connection_id)
    identity = db.get(ConnectionIdentity, decision.identity_id)
    mail = db.get(MailConnection, binding.mail_connection_id)
    source = db.get(SourceReference, binding.source_reference_id)
    source_current = db.get(SourceCurrent, binding.source_reference_id)
    source_version = db.get(SourceVersion, decision.source_version_id)
    if (not runtime or not identity or identity.record_version != decision.identity_record_version
            or not mail or mail.record_version != decision.mail_connection_record_version
            or runtime.mail_connection_id != message.mail_connection_id
            or binding.mail_connection_id != message.mail_connection_id
            or binding.provider_message_id != message.provider_message_id
            or binding.source_reference_id != message.source_reference_id
            or not source or source.organization_id != message.organization_id
            or source.identity_id != runtime.identity_id or source.namespace != "gmail"
            or source.object_kind != "message" or source.freshness != "fresh"
            or source.availability != "available"
            or source.record_version != decision.source_reference_record_version
            or not source_current
            or source_current.organization_id != message.organization_id
            or source_current.version_id != decision.source_version_id
            or not source_version or source_version.organization_id != message.organization_id
            or source_version.source_id != source.id
            or source_version.revision != decision.source_version_revision
            or source_version.revision != 1):
        _deny()
    provider_message_id, provider_thread_id = provider_locator(source)
    if (provider_message_id != source.external_id or provider_message_id != binding.provider_message_id
            or not _evidence_is_current(db, decision, source, source_version)):
        _deny()
    if not runtime.flags.primary_read or (action and not runtime.flags.actions):
        _deny()
    require_mailbox_authority(db, runtime=runtime, actor=actor,
                              permission="action" if action else "read")
    return MailboxRuntime(**{**runtime.__dict__,
        "provider_message_id": provider_message_id, "provider_thread_id": provider_thread_id,
        "source_reference_id": source.id, "source_version_id": source_version.id,
        "binding_id": binding.id})


def observe_gmail_message(db, *, runtime: MailboxRuntime, project_id: int,
                          provider_message_id: str, provider_thread_id: str | None,
                          observation_key: str):
    if (not provider_message_id or len(provider_message_id) > 500
            or (provider_thread_id is not None and (not provider_thread_id or len(provider_thread_id) > 500))
            or not observation_key or len(observation_key) > 200):
        _deny()
    locator = {"kind": "gmail_message", "provider_message_id": provider_message_id}
    if provider_thread_id is not None:
        locator["provider_thread_id"] = provider_thread_id
    source = db.scalar(select(SourceReference).where(
        SourceReference.organization_id == runtime.organization_id,
        SourceReference.identity_id == runtime.identity_id,
        SourceReference.namespace == "gmail", SourceReference.external_id == provider_message_id,
        SourceReference.incarnation == 1).with_for_update())
    now = datetime.now(timezone.utc)
    if source is None:
        source = SourceReference(organization_id=runtime.organization_id, origin_project_id=project_id,
            identity_id=runtime.identity_id, namespace="gmail", external_id=provider_message_id,
            external_id_kind="provider_message_id", incarnation=1, object_kind="message",
            canonical_locator=locator, record_version=1, freshness="fresh", sync_state="observed",
            availability="available", last_seen_at=now)
        db.add(source); db.flush()
    else:
        source.canonical_locator = locator
        source.freshness = "fresh"
        source.sync_state = "observed"
        source.availability = "available"
        source.last_seen_at = now
        source.record_version += 1
        db.flush()
    version = db.scalar(select(SourceVersion).where(
        SourceVersion.organization_id == runtime.organization_id,
        SourceVersion.source_id == source.id,
        SourceVersion.observation_key == observation_key))
    if version is None:
        version = SourceVersion(organization_id=runtime.organization_id, source_id=source.id,
            revision=1, observation_key=observation_key, consistency="metadata_only",
            locator_at_observation=locator, integrity=[], observed_at=now)
        db.add(version); db.flush()
    elif version.locator_at_observation != locator:
        _deny()
    current = db.get(SourceCurrent, source.id)
    if current is None:
        db.add(SourceCurrent(source_id=source.id, organization_id=runtime.organization_id,
                             version_id=version.id))
    elif current.version_id != version.id:
        current.version_id = version.id
    db.flush()
    return source, version
