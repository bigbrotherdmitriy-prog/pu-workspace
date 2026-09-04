"""Transactional mailbox identity, flags, ingress and reconciliation services."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select, update

from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.mailbox_identity import (
    MailboxAuthorityState, MailboxCredentialGeneration, MailboxCutoverFlags,
    MailboxOriginBinding, MailboxOriginCurrent, MailboxOriginDecision,
)
from app.models.v54_pilot import ConnectionIdentity, MailConnection, SourceReference, SourceVersion
from app.mailbox_identity.dto import ReconciliationCommand, ReconciliationResult


class MailboxConflict(ValueError):
    pass


def _fail(code="resource_unavailable"):
    raise MailboxConflict(code)


def _hash(command: ReconciliationCommand) -> str:
    value = command.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class MailboxIdentityService:
    """All methods join the caller transaction and only flush."""

    def bind_verified_google_subject(self, db, *, organization_id: int, google_token_id: int,
                                     subject: str, now=None):
        now = now or datetime.now(timezone.utc)
        if not subject or not str(subject).strip() or organization_id <= 0 or google_token_id <= 0:
            _fail()
        prior = db.scalar(select(MailboxCredentialGeneration).where(
            MailboxCredentialGeneration.organization_id == organization_id,
            MailboxCredentialGeneration.google_token_id == google_token_id,
            MailboxCredentialGeneration.state == "active",
        ).with_for_update())
        identity = db.scalar(select(ConnectionIdentity).where(
            ConnectionIdentity.organization_id == organization_id,
            ConnectionIdentity.provider == "google_workspace",
            ConnectionIdentity.account_key == subject,
        ).with_for_update())
        if prior:
            prior_identity = db.get(ConnectionIdentity, prior.connection_identity_id)
            if not prior_identity:
                _fail()
            if prior_identity.account_key == subject:
                if prior_identity.state != "verified": _fail()
            elif prior_identity.state != "revoked":
                _fail("explicit_revoke_required")
        if identity is None:
            identity = ConnectionIdentity(organization_id=organization_id, provider="google_workspace",
                account_key=subject, state="verified", binding_epoch=1, record_version=1,
                credential_generation=1, verified_at=now)
            db.add(identity); db.flush()
            generation = 1
        else:
            if identity.state != "verified":
                _fail()
            generation = int(identity.credential_generation or 0) + 1
            result = db.execute(update(ConnectionIdentity).where(
                ConnectionIdentity.id == identity.id,
                ConnectionIdentity.organization_id == organization_id,
                ConnectionIdentity.record_version == identity.record_version,
            ).values(credential_generation=generation, record_version=identity.record_version + 1,
                     verified_at=now).execution_options(synchronize_session="fetch"))
            if result.rowcount != 1: _fail("identity_version_conflict")
        if not prior or prior.generation != generation:
            if prior:
                # Credential generations are immutable. Revocation is represented by
                # the identity generation pin; no old row is edited.
                pass
            db.add(MailboxCredentialGeneration(organization_id=organization_id,
                connection_identity_id=identity.id, generation=generation,
                binding_epoch=identity.binding_epoch, google_token_id=google_token_id,
                state="active", verified_at=now))
        mail = db.scalar(select(MailConnection).where(
            MailConnection.organization_id == organization_id,
            MailConnection.identity_id == identity.id,
            MailConnection.namespace == "gmail",
        ))
        if mail is None:
            mail = MailConnection(organization_id=organization_id, identity_id=identity.id,
                                  namespace="gmail", state="active", record_version=1)
            db.add(mail)
        db.flush()
        if self.flags(db, organization_id=organization_id, mail_connection_id=mail.id,
                      generation=generation) is None:
            db.add(MailboxCutoverFlags(organization_id=organization_id,
                mail_connection_id=mail.id, credential_generation=generation))
            db.flush()
        return identity, mail, generation

    @staticmethod
    def flags(db, *, organization_id, mail_connection_id, generation):
        row = db.scalar(select(MailboxCutoverFlags).where(
            MailboxCutoverFlags.organization_id == organization_id,
            MailboxCutoverFlags.mail_connection_id == mail_connection_id,
            MailboxCutoverFlags.credential_generation == generation,
        ))
        return row

    @staticmethod
    def _authority(db, command, organization_id):
        row = db.scalar(select(MailboxAuthorityState).where(
            MailboxAuthorityState.organization_id == organization_id,
            MailboxAuthorityState.mail_connection_id == command.mail_connection_id,
            MailboxAuthorityState.principal_kind == "user",
            MailboxAuthorityState.principal_id == str(command.actor_user_id),
        ).with_for_update())
        now = datetime.now(timezone.utc)
        valid = row.valid_until if row else None
        if valid and valid.tzinfo is None: valid = valid.replace(tzinfo=timezone.utc)
        if (not row or row.state != "active" or "reconcile" not in row.permissions
                or row.authority_version != command.authority_version or not valid or valid <= now):
            _fail()

    def reconcile(self, db, command: ReconciliationCommand) -> ReconciliationResult:
        command = ReconciliationCommand.model_validate(command)
        payload_hash = _hash(command)
        msg = db.scalar(select(Message).where(Message.id == command.message_id).with_for_update())
        if not msg: _fail()
        existing = db.scalar(select(MailboxOriginDecision).where(
            MailboxOriginDecision.organization_id == msg.organization_id,
            MailboxOriginDecision.decision_key == command.decision_key).with_for_update())
        if existing:
            if existing.payload_hash != payload_hash: _fail("idempotency_conflict")
            binding = db.scalar(select(MailboxOriginBinding).where(MailboxOriginBinding.decision_id == existing.id))
            return ReconciliationResult(decision_id=existing.id, binding_id=binding.id,
                origin_version=existing.expected_message_version + 1, state=binding.state,
                idempotent_replay=True)
        if msg.origin_version != command.expected_message_origin_version: _fail("origin_version_conflict")
        self._authority(db, command, msg.organization_id)
        identity = db.scalar(select(ConnectionIdentity).where(
            ConnectionIdentity.id == command.identity_id,
            ConnectionIdentity.organization_id == msg.organization_id).with_for_update())
        mail = db.scalar(select(MailConnection).where(
            MailConnection.id == command.mail_connection_id,
            MailConnection.organization_id == msg.organization_id).with_for_update())
        generation = db.scalar(select(MailboxCredentialGeneration).where(
            MailboxCredentialGeneration.organization_id == msg.organization_id,
            MailboxCredentialGeneration.connection_identity_id == command.identity_id,
            MailboxCredentialGeneration.generation == command.credential_generation,
            MailboxCredentialGeneration.binding_epoch == command.binding_epoch,
            MailboxCredentialGeneration.state == "active"))
        source_version = db.scalar(select(SourceVersion).where(
            SourceVersion.id == command.source_version_id,
            SourceVersion.organization_id == msg.organization_id,
            SourceVersion.source_id == command.source_reference_id))
        source = db.get(SourceReference, command.source_reference_id)
        if (not identity or identity.state != "verified" or identity.binding_epoch != command.binding_epoch
                or identity.credential_generation != command.credential_generation or not generation
                or not mail or mail.identity_id != identity.id or mail.state != "active"
                or not source or source.organization_id != msg.organization_id or source.identity_id != identity.id
                or source.namespace != mail.namespace or source.object_kind != "message" or not source_version):
            _fail()
        current = db.scalar(select(MailboxOriginCurrent).where(
            MailboxOriginCurrent.message_id == msg.id).with_for_update())
        current_version = current.record_version if current else 1
        if current_version != command.expected_current_origin_version: _fail("current_origin_version_conflict")
        decision = MailboxOriginDecision(organization_id=msg.organization_id,
            decision_key=command.decision_key, payload_hash=payload_hash, message_id=msg.id,
            expected_message_version=command.expected_message_origin_version,
            expected_current_version=command.expected_current_origin_version,
            identity_id=identity.id, mail_connection_id=mail.id,
            binding_epoch=command.binding_epoch, credential_generation=command.credential_generation,
            source_reference_id=source.id, source_version_id=source_version.id,
            evidence_refs=list(command.evidence_refs),
            reason_code=command.reason_code, correlation_id=command.correlation_id,
            decided_by_user_id=command.actor_user_id, authority_version=command.authority_version,
            outcome=command.outcome, created_at=datetime.now(timezone.utc))
        db.add(decision); db.flush()
        old_binding = db.get(MailboxOriginBinding, current.binding_id) if current and current.binding_id else None
        state = {"CONFIRM": "confirmed", "REJECT": "rejected", "LEAVE_UNRESOLVED": "unresolved"}[command.outcome]
        binding = MailboxOriginBinding(organization_id=msg.organization_id,
            lineage_id=old_binding.lineage_id if old_binding else str(uuid4()),
            revision=(old_binding.revision + 1 if old_binding else 1), message_id=msg.id,
            mail_connection_id=mail.id if state == "confirmed" else None,
            provider_message_id=source.external_id if state == "confirmed" else None,
            source_reference_id=source.id if state == "confirmed" else None,
            binding_epoch=command.binding_epoch, credential_generation=command.credential_generation,
            state=state, decision_id=decision.id, created_at=datetime.now(timezone.utc))
        db.add(binding); db.flush()
        values = dict(origin_version=msg.origin_version + 1)
        if state == "confirmed":
            values.update(mail_connection_id=mail.id, provider_message_id=source.external_id,
                          source_reference_id=source.id)
        result = db.execute(update(Message).where(Message.id == msg.id,
            Message.origin_version == command.expected_message_origin_version).values(**values)
            .execution_options(synchronize_session="fetch"))
        if result.rowcount != 1: _fail("origin_version_conflict")
        if current:
            result = db.execute(update(MailboxOriginCurrent).where(
                MailboxOriginCurrent.message_id == msg.id,
                MailboxOriginCurrent.record_version == command.expected_current_origin_version,
            ).values(binding_id=binding.id, record_version=current.record_version + 1)
             .execution_options(synchronize_session="fetch"))
            if result.rowcount != 1: _fail("current_origin_version_conflict")
        else:
            db.add(MailboxOriginCurrent(message_id=msg.id, organization_id=msg.organization_id,
                                        binding_id=binding.id, record_version=2))
        # Safe audit contains no provider/account/message/email/attachment identifier.
        db.add(AuditLog(action="mailbox_origin_reconciled", entity_type="message",
                        entity_id=msg.id, details=f"outcome={command.outcome}"))
        db.flush()
        return ReconciliationResult(decision_id=decision.id, binding_id=binding.id,
                                    origin_version=msg.origin_version, state=state)
