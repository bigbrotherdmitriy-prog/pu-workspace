"""Transactional mailbox identity, flags, ingress and reconciliation services."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select, update

from app.mailbox_identity.runtime import (
    provider_locator, require_mailbox_authority, rollout_flags_are_valid,
)
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.mailbox_identity import (
    MailboxAuthorityState, MailboxCredentialGeneration, MailboxCutoverFlags,
    MailboxOriginBinding, MailboxOriginCurrent, MailboxOriginDecision,
)
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_pilot import (
    ConnectionIdentity, Evidence, EvidenceAssessment, MailConnection,
    SourceCurrent, SourceReference, SourceVersion,
)
from app.mailbox_identity.dto import (
    MailboxRolloutResult, MailboxRolloutTransition,
    ReconciliationCommand, ReconciliationResult,
)


class MailboxConflict(ValueError):
    pass


def _fail(code="resource_unavailable"):
    raise MailboxConflict(code)


def _hash(command: ReconciliationCommand) -> str:
    value = command.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


ROLE_LEVEL = {"viewer": 10, "member": 20, "editor": 30, "manager": 40, "owner": 50}


def _trusted_actor(db, actor: User):
    if not actor or not actor.id or db.get(User, actor.id) is not actor:
        _fail()


def _project_access(db, actor: User, *, organization_id: int, project_id: int):
    project = db.get(Project, project_id)
    role = db.scalar(select(ProjectMember.role).where(
        ProjectMember.project_id == project_id, ProjectMember.user_id == actor.id))
    if (not project or project.organization_id != organization_id
            or ROLE_LEVEL.get(role, 0) < ROLE_LEVEL["editor"]):
        _fail()


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


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

    def change_rollout_flags(self, db, command: MailboxRolloutTransition, *, actor: User,
                             expected_record_version: int) -> MailboxRolloutResult:
        """CAS one human-confirmed flag transition in the caller transaction."""
        command = MailboxRolloutTransition.model_validate(command)
        _trusted_actor(db, actor)
        if type(expected_record_version) is not int or expected_record_version <= 0:
            _fail("flags_version_conflict")

        # Match the credential rotation lock order: generation, identity, then
        # connection. This makes rotation and rollout mutually serializable.
        mail_hint = db.scalar(select(MailConnection).where(
            MailConnection.organization_id == command.organization_id,
            MailConnection.id == command.mail_connection_id,
        ))
        generation = db.scalar(select(MailboxCredentialGeneration).where(
            MailboxCredentialGeneration.organization_id == command.organization_id,
            MailboxCredentialGeneration.connection_identity_id == (
                mail_hint.identity_id if mail_hint else None
            ),
            MailboxCredentialGeneration.generation == command.credential_generation,
            MailboxCredentialGeneration.binding_epoch == command.binding_epoch,
            MailboxCredentialGeneration.state == "active",
        ).with_for_update())
        identity = db.scalar(select(ConnectionIdentity).where(
            ConnectionIdentity.organization_id == command.organization_id,
            ConnectionIdentity.id == (generation.connection_identity_id if generation else None),
        ).with_for_update())
        mail = db.scalar(select(MailConnection).where(
            MailConnection.organization_id == command.organization_id,
            MailConnection.id == command.mail_connection_id,
        ).with_for_update())
        if (not mail or mail.state != "active" or mail.namespace != "gmail"
                or not identity or identity.organization_id != command.organization_id
                or identity.state != "verified"
                or identity.binding_epoch != command.binding_epoch
                or identity.credential_generation != command.credential_generation
                or not generation):
            _fail()

        flags = db.scalar(select(MailboxCutoverFlags).where(
            MailboxCutoverFlags.organization_id == command.organization_id,
            MailboxCutoverFlags.mail_connection_id == command.mail_connection_id,
            MailboxCutoverFlags.credential_generation == command.credential_generation,
        ).with_for_update())
        if not flags or flags.record_version != expected_record_version:
            _fail("flags_version_conflict")
        try:
            runtime = type("RolloutRuntime", (), {
                "organization_id": command.organization_id,
                "mail_connection_id": command.mail_connection_id,
            })()
            require_mailbox_authority(
                db,
                runtime=runtime,
                actor=actor,
                permission="rollout",
                expected_version=command.authority_version,
            )
        except ValueError:
            _fail()

        if not rollout_flags_are_valid(flags) or getattr(flags, command.flag) is command.enabled:
            _fail()
        next_values = {
            name: getattr(flags, name) for name in (
                "shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions"
            )
        }
        next_values[command.flag] = command.enabled
        candidate = type("RolloutCandidate", (), next_values)()
        if not rollout_flags_are_valid(candidate):
            _fail()

        next_version = expected_record_version + 1
        result = db.execute(update(MailboxCutoverFlags).where(
            MailboxCutoverFlags.id == flags.id,
            MailboxCutoverFlags.organization_id == command.organization_id,
            MailboxCutoverFlags.mail_connection_id == command.mail_connection_id,
            MailboxCutoverFlags.credential_generation == command.credential_generation,
            MailboxCutoverFlags.record_version == expected_record_version,
        ).values(**{command.flag: command.enabled}, record_version=next_version)
         .execution_options(synchronize_session="fetch"))
        if result.rowcount != 1:
            _fail("flags_version_conflict")
        db.add(AuditLog(
            action="mailbox_rollout_transition_confirmed",
            entity_type="mailbox_cutover_flags",
            entity_id=flags.id,
            details=(f"flag={command.flag};enabled={str(command.enabled).lower()};"
                     f"from_version={expected_record_version};to_version={next_version};"
                     f"actor_user_id={actor.id}"),
        ))
        db.flush()
        db.refresh(flags)
        return MailboxRolloutResult(
            flag=command.flag,
            enabled=command.enabled,
            record_version=flags.record_version,
            shadow_write=flags.shadow_write,
            shadow_read_compare=flags.shadow_read_compare,
            pilot_write=flags.pilot_write,
            primary_read=flags.primary_read,
            actions=flags.actions,
        )

    @staticmethod
    def _authority(db, command, organization_id, actor):
        runtime = type("AuthorityRuntime", (), {
            "organization_id": organization_id,
            "mail_connection_id": command.mail_connection_id,
        })()
        try:
            return require_mailbox_authority(
                db, runtime=runtime, actor=actor, permission="reconcile",
                expected_version=command.authority_version)
        except ValueError:
            _fail()

    def _validated_lineage(self, db, command, msg, actor):
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
        source = db.scalar(select(SourceReference).where(
            SourceReference.id == command.source_reference_id,
            SourceReference.organization_id == msg.organization_id).with_for_update())
        source_version = db.scalar(select(SourceVersion).where(
            SourceVersion.id == command.source_version_id,
            SourceVersion.organization_id == msg.organization_id,
            SourceVersion.source_id == command.source_reference_id))
        source_current = db.get(SourceCurrent, command.source_reference_id)
        if (not identity or identity.state != "verified"
                or identity.record_version != command.identity_record_version
                or identity.binding_epoch != command.binding_epoch
                or identity.credential_generation != command.credential_generation or not generation
                or not mail or mail.identity_id != identity.id or mail.state != "active"
                or mail.record_version != command.mail_connection_record_version
                or not source or source.identity_id != identity.id or source.namespace != mail.namespace
                or source.object_kind != "message" or source.freshness != "fresh"
                or source.availability != "available"
                or source.record_version != command.source_reference_record_version
                or not source_version or source_version.revision != command.source_version_revision
                or command.source_version_revision != 1 or not source_current
                or source_current.organization_id != msg.organization_id
                or source_current.version_id != source_version.id):
            _fail()
        provider_message_id, _thread_id = provider_locator(source)
        if provider_message_id != source.external_id:
            _fail()
        _project_access(db, actor, organization_id=msg.organization_id, project_id=msg.project_id)
        _project_access(db, actor, organization_id=msg.organization_id, project_id=source.origin_project_id)
        now = datetime.now(timezone.utc)
        for pin in command.evidence_refs:
            row = db.execute(select(Evidence, EvidenceAssessment).join(
                EvidenceAssessment, EvidenceAssessment.evidence_id == Evidence.id).where(
                Evidence.id == pin.evidence_id,
                Evidence.organization_id == msg.organization_id)).first()
            evidence, assessment = row if row else (None, None)
            valid_until = _aware(assessment.valid_until) if assessment else None
            if (not evidence or evidence.source_id != source.id
                    or evidence.source_version_id != source_version.id
                    or evidence.revision != pin.evidence_revision
                    or not assessment or assessment.record_version != pin.assessment_record_version
                    or assessment.verification != "verified" or assessment.freshness != "fresh"
                    or assessment.availability != "available" or not assessment.reviewed_by
                    or not valid_until or valid_until <= now):
                _fail()
        return identity, mail, source, source_version

    def reconcile(self, db, command: ReconciliationCommand, *, actor: User) -> ReconciliationResult:
        command = ReconciliationCommand.model_validate(command)
        _trusted_actor(db, actor)
        payload_hash = _hash(command)
        msg = db.scalar(select(Message).where(Message.id == command.message_id).with_for_update())
        if not msg:
            _fail()
        _project_access(db, actor, organization_id=msg.organization_id, project_id=msg.project_id)
        self._authority(db, command, msg.organization_id, actor)
        existing = db.scalar(select(MailboxOriginDecision).where(
            MailboxOriginDecision.organization_id == msg.organization_id,
            MailboxOriginDecision.decision_key == command.decision_key).with_for_update())
        if existing:
            if existing.payload_hash != payload_hash:
                _fail("idempotency_conflict")
            if existing.decided_by_user_id != actor.id:
                _fail()
            binding = db.scalar(select(MailboxOriginBinding).where(
                MailboxOriginBinding.organization_id == msg.organization_id,
                MailboxOriginBinding.decision_id == existing.id))
            if not binding:
                _fail()
            return ReconciliationResult(decision_id=existing.id, binding_id=binding.id,
                origin_version=existing.expected_message_version + 1, state=binding.state,
                idempotent_replay=True)
        if msg.origin_version != command.expected_message_origin_version:
            _fail("origin_version_conflict")
        identity, mail, source, source_version = self._validated_lineage(db, command, msg, actor)
        current = db.scalar(select(MailboxOriginCurrent).where(
            MailboxOriginCurrent.organization_id == msg.organization_id,
            MailboxOriginCurrent.message_id == msg.id).with_for_update())
        current_version = current.record_version if current else 1
        if current_version != command.expected_current_origin_version:
            _fail("current_origin_version_conflict")
        decision = MailboxOriginDecision(organization_id=msg.organization_id,
            decision_key=command.decision_key, payload_hash=payload_hash, message_id=msg.id,
            expected_message_version=command.expected_message_origin_version,
            expected_current_version=command.expected_current_origin_version,
            identity_id=identity.id, identity_record_version=identity.record_version,
            mail_connection_id=mail.id, mail_connection_record_version=mail.record_version,
            binding_epoch=command.binding_epoch, credential_generation=command.credential_generation,
            source_reference_id=source.id, source_reference_record_version=source.record_version,
            source_version_id=source_version.id, source_version_revision=source_version.revision,
            evidence_refs=[pin.model_dump(mode="json") for pin in command.evidence_refs],
            reason_code=command.reason_code, correlation_id=command.correlation_id,
            decided_by_user_id=actor.id, authority_version=command.authority_version,
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
        values = dict(origin_version=msg.origin_version + 1,
                      mail_connection_id=None, provider_message_id=None, source_reference_id=None)
        if state == "confirmed":
            values.update(mail_connection_id=mail.id, provider_message_id=source.external_id,
                          source_reference_id=source.id)
        result = db.execute(update(Message).where(Message.id == msg.id,
            Message.origin_version == command.expected_message_origin_version).values(**values)
            .execution_options(synchronize_session="fetch"))
        if result.rowcount != 1:
            _fail("origin_version_conflict")
        if current:
            result = db.execute(update(MailboxOriginCurrent).where(
                MailboxOriginCurrent.organization_id == msg.organization_id,
                MailboxOriginCurrent.message_id == msg.id,
                MailboxOriginCurrent.record_version == command.expected_current_origin_version,
            ).values(binding_id=binding.id, record_version=current.record_version + 1)
             .execution_options(synchronize_session="fetch"))
            if result.rowcount != 1:
                _fail("current_origin_version_conflict")
        else:
            db.add(MailboxOriginCurrent(message_id=msg.id, organization_id=msg.organization_id,
                                        binding_id=binding.id, record_version=2))
        db.add(AuditLog(action="mailbox_origin_reconciled", entity_type="message",
                        entity_id=msg.id, details=f"outcome={command.outcome}"))
        db.flush()
        return ReconciliationResult(decision_id=decision.id, binding_id=binding.id,
                                    origin_version=msg.origin_version, state=state)

    def record_provider_observed_origin(self, db, *, message: Message, runtime,
                                        source: SourceReference, source_version: SourceVersion,
                                        actor: User):
        """Append the exact provider observation before mailbox ingress commits."""
        _trusted_actor(db, actor)
        msg = db.scalar(select(Message).where(Message.id == message.id).with_for_update())
        if not msg or msg.organization_id != runtime.organization_id:
            _fail()
        _project_access(db, actor, organization_id=msg.organization_id, project_id=msg.project_id)
        authority = require_mailbox_authority(db, runtime=runtime, actor=actor, permission="ingest")
        identity = db.get(ConnectionIdentity, runtime.identity_id)
        mail = db.get(MailConnection, runtime.mail_connection_id)
        provider_message_id, _thread_id = provider_locator(source)
        source_current = db.get(SourceCurrent, source.id)
        if (not identity or identity.state != "verified" or identity.binding_epoch != runtime.binding_epoch
                or identity.credential_generation != runtime.generation
                or not mail or mail.identity_id != identity.id or mail.state != "active"
                or source.organization_id != runtime.organization_id or source.identity_id != runtime.identity_id
                or source.namespace != "gmail" or source.object_kind != "message"
                or source.freshness != "fresh" or source.availability != "available"
                or provider_message_id != source.external_id or not source_current
                or source_current.version_id != source_version.id
                or source_version.organization_id != runtime.organization_id
                or source_version.source_id != source.id or source_version.revision != 1
                or msg.mail_connection_id != runtime.mail_connection_id
                or msg.provider_message_id != provider_message_id
                or msg.source_reference_id != source.id):
            _fail()
        current = db.scalar(select(MailboxOriginCurrent).where(
            MailboxOriginCurrent.organization_id == msg.organization_id,
            MailboxOriginCurrent.message_id == msg.id).with_for_update())
        old_binding = db.get(MailboxOriginBinding, current.binding_id) if current and current.binding_id else None
        if old_binding:
            old_decision = db.get(MailboxOriginDecision, old_binding.decision_id)
            if (old_binding.state == "confirmed" and old_decision
                    and old_decision.source_version_id == source_version.id
                    and old_decision.source_reference_record_version == source.record_version
                    and old_decision.identity_record_version == identity.record_version
                    and old_decision.mail_connection_record_version == mail.record_version
                    and old_binding.binding_epoch == runtime.binding_epoch
                    and old_binding.credential_generation == runtime.generation):
                return old_binding
        decision_key = (f"provider_ingress:{msg.id}:{source_version.id}:"
                        f"{runtime.generation}:{source.record_version}")
        payload_hash = hashlib.sha256(json.dumps({
            "message_id": msg.id, "source_version_id": source_version.id,
            "generation": runtime.generation,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        decision = MailboxOriginDecision(organization_id=msg.organization_id,
            decision_key=decision_key, payload_hash=payload_hash, message_id=msg.id,
            expected_message_version=msg.origin_version,
            expected_current_version=current.record_version if current else 1,
            identity_id=runtime.identity_id, identity_record_version=identity.record_version,
            mail_connection_id=runtime.mail_connection_id,
            mail_connection_record_version=mail.record_version,
            binding_epoch=runtime.binding_epoch, credential_generation=runtime.generation,
            source_reference_id=source.id, source_reference_record_version=source.record_version,
            source_version_id=source_version.id, source_version_revision=source_version.revision,
            evidence_refs=[], reason_code="provider_observed_ingress",
            correlation_id=f"provider_ingress:{msg.id}", decided_by_user_id=actor.id,
            authority_version=authority.authority_version, outcome="CONFIRM",
            created_at=datetime.now(timezone.utc))
        db.add(decision); db.flush()
        binding = MailboxOriginBinding(organization_id=msg.organization_id,
            lineage_id=old_binding.lineage_id if old_binding else str(uuid4()),
            revision=old_binding.revision + 1 if old_binding else 1, message_id=msg.id,
            mail_connection_id=runtime.mail_connection_id, provider_message_id=provider_message_id,
            source_reference_id=source.id, binding_epoch=runtime.binding_epoch,
            credential_generation=runtime.generation, state="confirmed", decision_id=decision.id,
            created_at=datetime.now(timezone.utc))
        db.add(binding); db.flush()
        if current:
            result = db.execute(update(MailboxOriginCurrent).where(
                MailboxOriginCurrent.organization_id == msg.organization_id,
                MailboxOriginCurrent.message_id == msg.id,
                MailboxOriginCurrent.record_version == current.record_version,
            ).values(binding_id=binding.id, record_version=current.record_version + 1)
             .execution_options(synchronize_session="fetch"))
            if result.rowcount != 1:
                _fail("current_origin_version_conflict")
            result = db.execute(update(Message).where(
                Message.id == msg.id, Message.origin_version == msg.origin_version).values(
                origin_version=msg.origin_version + 1).execution_options(synchronize_session="fetch"))
            if result.rowcount != 1:
                _fail("origin_version_conflict")
        else:
            db.add(MailboxOriginCurrent(message_id=msg.id, organization_id=msg.organization_id,
                                        binding_id=binding.id, record_version=1))
        db.add(AuditLog(action="mailbox_origin_observed", entity_type="message",
                        entity_id=msg.id, details="status=observed"))
        db.flush()
        return binding
