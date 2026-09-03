"""Foundation-backed synthetic pilot. All helpers join a caller transaction.

The caller MUST roll back on any exception, including a failed audit/Trust call.
No method starts/commits/rolls back a transaction, calls a provider, enqueues a
job, writes a claim/Task/approval/receipt, or interprets source text as authority.
Resolver and Trust are injected foundation Protocols, never allow-all defaults.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
import re
from typing import Callable
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.v54_dto import ActionEnvelope, canonical_hash, canonical_json
from app.core.v54_interfaces import (
    AuditAppend, ContextConfirmation, PilotGate, RequestScope, Resolver,
    TrustWriter, require_resolution,
)
from app.core.v54_refs import ObjectRef, TaggedId, VersionPin, require_same_tenant
from app.core.v54_transactions import append_audit
from app.models.ai_secretary import Message
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.task import Task
from app.models.v54_pilot import (
    ActionApproval, ActionReceipt, ActionRevision, AuditExtension, ConnectionIdentity,
    ContextRelation, DeadlineClaim, Evidence, MailConnection, PilotAction, SourceCurrent,
    SourceReference, SourceVersion,
)


class ContextError(ValueError):
    """Safe, content-free reason code; never includes provider/SQL input."""


def boundary(method):
    @wraps(method)
    def call(self, db, *, scope, **kwargs):
        try:
            self._entry(db, scope)
            return method(self, db, scope=scope, **kwargs)
        except ContextError:
            raise
        except SQLAlchemyError:
            raise ContextError("context_transaction_conflict") from None
        except (ValueError, TypeError, KeyError):
            raise ContextError("resource_unavailable") from None
        except RuntimeError:
            raise ContextError("context_dependency_failed") from None
    return call


class ContextCommunication:
    def __init__(self, *, resolver: Resolver, gate: PilotGate,
                 authorize_audit: Callable[[Session, RequestScope, ObjectRef], bool],
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.resolver, self.gate = resolver, gate
        self.authorize_audit, self.clock = authorize_audit, clock

    def _entry(self, db, scope):
        if not db.in_transaction():
            raise ContextError("caller_transaction_required")
        # Server-generated correlation only. Never accept a body/model instruction here.
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", scope.correlation_id):
            raise ContextError("invalid_correlation_id")
        self.gate.require_confirm(mode="CONFIRM", action_type="task.internal.create", now=self.clock())

    @staticmethod
    def _ref(scope, kind, value):
        return ObjectRef(namespace="pu", type=kind, tenant_id=scope.tenant,
                         id=TaggedId(kind="int" if kind in {"message", "project", "contract", "task", "user"}
                                     else "uuid", value=str(value)))

    @staticmethod
    def _pin(ref, value=1, kind="record_version"):
        return VersionPin(ref=ref, version_kind=kind, value=value)

    def _allow(self, db, scope, pin, operation="review", lock=True):
        require_same_tenant(scope.tenant, pin.ref)
        result = self.resolver.resolve(db, scope=scope, pin=pin, operation=operation, lock=lock)
        require_resolution(result, scope=scope, pin=pin, operation=operation, now=self.clock())

    @staticmethod
    def _row(db, model, key, *, lock=True):
        query = select(model).where(model.id == key).execution_options(populate_existing=True)
        return db.scalar(query.with_for_update() if lock else query)

    def _project(self, db, scope, pin, *, lock=True):
        if pin.ref.type != "project" or pin.version_kind != "record_version":
            raise ContextError("resource_unavailable")
        self._allow(db, scope, pin, lock=lock)
        row = self._row(db, Project, int(pin.ref.id.value), lock=lock)
        if not row or row.organization_id != int(scope.tenant.value) or row.record_version != pin.value:
            raise ContextError("resource_unavailable")
        return row

    def _source(self, db, scope, pin, *, lock=True):
        if pin.ref.type != "source" or pin.version_kind != "record_version":
            raise ContextError("resource_unavailable")
        self._allow(db, scope, pin, lock=lock)
        source = self._row(db, SourceReference, pin.ref.id.value, lock=lock)
        if not source or source.organization_id != int(scope.tenant.value) or source.record_version != pin.value:
            raise ContextError("resource_unavailable")
        project = self._row(db, Project, source.origin_project_id, lock=False)
        if not project:
            raise ContextError("resource_unavailable")
        self._project(db, scope, self._pin(self._ref(scope, "project", project.id), project.record_version), lock=lock)
        return source

    def _mail(self, db, scope, pin, *, lock=True):
        if pin.ref.type != "mail_connection" or pin.version_kind != "record_version":
            raise ContextError("resource_unavailable")
        self._allow(db, scope, pin, lock=lock)
        mail = self._row(db, MailConnection, pin.ref.id.value, lock=False)
        if (not mail or mail.organization_id != int(scope.tenant.value)
                or mail.record_version != pin.value or mail.state != "active"):
            raise ContextError("resource_unavailable")
        identity = self._row(db, ConnectionIdentity, mail.identity_id, lock=lock)
        if (not identity or identity.organization_id != int(scope.tenant.value)
                or identity.state != "verified" or identity.provider != "synthetic"):
            raise ContextError("resource_unavailable")
        self._allow(db, scope, self._pin(self._ref(scope, "connection_identity", identity.id),
                                       identity.record_version), lock=lock)
        # Pilot mailbox writers serialize identity -> mailbox -> Message, never
        # Message -> mailbox (which would deadlock against duplicate ingress).
        mail = self._row(db, MailConnection, pin.ref.id.value, lock=lock)
        if mail.record_version != pin.value or mail.state != "active":
            raise ContextError("resource_unavailable")
        return mail

    def _message(self, db, scope, ref, *, lock=True, in_scope=True):
        if ref.type != "message":
            raise ContextError("resource_unavailable")
        require_same_tenant(scope.tenant, ref)
        # Message has context_version, not a generic record_version. Explicit bridge.
        msg = self._row(db, Message, int(ref.id.value), lock=False)
        if (not msg or msg.organization_id != int(scope.tenant.value)
                or (in_scope and msg.project_id != int(scope.project.id.value))):
            raise ContextError("resource_unavailable")
        self._allow(db, scope, self._pin(ref, msg.context_version), lock=False)
        if not msg.mail_connection_id or not msg.source_reference_id or msg.source_type != "synthetic":
            raise ContextError("legacy_origin_unresolved")
        mail = self._row(db, MailConnection, msg.mail_connection_id, lock=False)
        source = self._row(db, SourceReference, msg.source_reference_id, lock=False)
        if not mail or not source:
            raise ContextError("resource_unavailable")
        self._mail(db, scope, self._pin(self._ref(scope, "mail_connection", mail.id), mail.record_version), lock=lock)
        msg = self._row(db, Message, int(ref.id.value), lock=lock)
        if (msg.organization_id != int(scope.tenant.value) or msg.mail_connection_id != mail.id
                or msg.source_reference_id != source.id
                or (in_scope and msg.project_id != int(scope.project.id.value))):
            raise ContextError("resource_unavailable")
        self._allow(db, scope, self._pin(ref, msg.context_version), lock=lock)
        self._source(db, scope, self._pin(self._ref(scope, "source", source.id), source.record_version), lock=lock)
        project = self._row(db, Project, msg.project_id, lock=False)
        if not project:
            raise ContextError("resource_unavailable")
        self._project(db, scope, self._pin(self._ref(scope, "project", project.id), project.record_version), lock=lock)
        if (source.identity_id != mail.identity_id or source.namespace != mail.namespace
                or source.external_id != msg.provider_message_id or source.object_kind != "message"):
            raise ContextError("resource_unavailable")
        return msg

    def _evidence(self, db, scope, msg, pins, *, lock=True):
        if not pins:
            raise ContextError("evidence_required")
        keys = [canonical_json(p.model_dump(mode="json")) for p in pins]
        if keys != sorted(set(keys)):
            raise ContextError("invalid_evidence_pins")
        for pin in pins:
            if pin.ref.type != "evidence" or pin.version_kind != "revision":
                raise ContextError("resource_unavailable")
            self._allow(db, scope, pin, lock=lock)
            evidence = self._row(db, Evidence, pin.ref.id.value, lock=lock)
            if not evidence or evidence.organization_id != msg.organization_id or evidence.revision != pin.value:
                raise ContextError("resource_unavailable")
            source = self._row(db, SourceReference, evidence.source_id, lock=False)
            if not source:
                raise ContextError("resource_unavailable")
            self._source(db, scope, self._pin(self._ref(scope, "source", source.id), source.record_version), lock=lock)
            if source.id != msg.source_reference_id and not (
                    source.object_kind == "attachment" and source.parent_source_id == msg.source_reference_id):
                raise ContextError("evidence_origin_mismatch")
            observation = self._row(db, SourceVersion, evidence.source_version_id, lock=lock)
            current = db.get(SourceCurrent, evidence.source_id, populate_existing=True)
            if (not observation or observation.source_id != evidence.source_id
                    or not current or current.version_id != observation.id):
                raise ContextError("resource_unavailable")
            self._allow(db, scope, self._pin(self._ref(scope, "source_version", observation.id), 1, "revision"), lock=lock)

    def _targets(self, db, scope, project, contract, *, lock=True):
        self._project(db, scope, project, lock=lock)
        if contract:
            if contract.ref.type != "contract" or contract.version_kind != "record_version":
                raise ContextError("resource_unavailable")
            self._allow(db, scope, contract, lock=lock)
            row = self._row(db, Contract, int(contract.ref.id.value), lock=lock)
            if not row or row.project_id != int(project.ref.id.value) or row.record_version != contract.value:
                raise ContextError("contract_project_mismatch")

    def _audit(self, db, scope, subject, event, relations=(), receipt=None):
        # Caller already owns the subject lock (Message or the identity's new mailbox).
        sequence = db.scalar(select(func.max(AuditExtension.sequence)).where(
            AuditExtension.organization_id == int(scope.tenant.value),
            AuditExtension.subject_type == subject.type, AuditExtension.subject_id == subject.id.value)) or 0
        return append_audit(db, scope=scope, event=AuditAppend(
            subject=subject, sequence=sequence + 1, event=event,
            relations=tuple(relations), receipt=receipt), authorize=self.authorize_audit)

    @boundary
    def extend_mail_connection(self, db, *, scope, source: VersionPin) -> VersionPin:
        """Namespace comes from an authorized existing source, not an account DTO."""
        source_row = self._source(db, scope, source, lock=False)
        identity = self._row(db, ConnectionIdentity, source_row.identity_id)
        if (not identity or identity.organization_id != int(scope.tenant.value)
                or identity.state != "verified" or identity.provider != "synthetic"
                or source_row.object_kind != "message"):
            raise ContextError("resource_unavailable")
        self._allow(db, scope, self._pin(self._ref(scope, "connection_identity", identity.id), identity.record_version))
        source_row = self._source(db, scope, source)
        mail = db.scalar(select(MailConnection).where(
            MailConnection.identity_id == identity.id, MailConnection.namespace == source_row.namespace)
            .with_for_update().execution_options(populate_existing=True))
        if mail:
            result = self._pin(self._ref(scope, "mail_connection", mail.id), mail.record_version)
            self._mail(db, scope, result)
            return result  # Never silently reactivate a blocked/revoked extension.
        mail = MailConnection(organization_id=identity.organization_id, identity_id=identity.id,
                              namespace=source_row.namespace, state="blocked")
        db.add(mail)
        db.flush()
        self._allow(db, scope, self._pin(self._ref(scope, "mail_connection", mail.id), mail.record_version))
        mail.state, mail.record_version = "active", mail.record_version + 1
        db.flush()
        self._audit(db, scope, self._ref(scope, "mail_connection", mail.id), "SOURCE_OBSERVED")
        return self._pin(self._ref(scope, "mail_connection", mail.id), mail.record_version)

    @boundary
    def register(self, db, *, scope, mailbox: VersionPin, source: VersionPin,
                 attachment: VersionPin) -> ObjectRef:
        mail = self._mail(db, scope, mailbox)
        # Inspect before Message locking without taking Source locks in reverse
        # order to the Context/Trust writers. Revalidate new ingress below.
        origin, attached = self._source(db, scope, source, lock=False), self._source(db, scope, attachment, lock=False)
        if (origin.object_kind != "message" or attached.object_kind != "attachment"
                or attached.parent_source_id != origin.id
                or any(s.identity_id != mail.identity_id or s.namespace != mail.namespace for s in (origin, attached))):
            raise ContextError("resource_unavailable")
        existing = db.scalar(select(Message).where(Message.mail_connection_id == mail.id,
                                                   Message.provider_message_id == origin.external_id))
        if existing:
            existing = self._message(db, scope, self._ref(scope, "message", existing.id), in_scope=False)
            if existing.source_reference_id != origin.id:
                raise ContextError("origin_conflict")
            return self._ref(scope, "message", existing.id)
        # Do not salt raw IDs, repoint legacy messages, or remove the global index.
        if db.scalar(select(Message.id).where(Message.source_type == "synthetic",
                                              Message.source_external_id == origin.external_id)) is not None:
            raise ContextError("legacy_mailbox_cutover_required")
        if origin.origin_project_id != int(scope.project.id.value):
            raise ContextError("explicit_intake_project_required")
        self._source(db, scope, source)
        self._source(db, scope, attachment)
        msg = Message(organization_id=int(scope.tenant.value), project_id=origin.origin_project_id,
                      contract_id=None, created_by_user_id=int(scope.actor.id.value), source_type="synthetic",
                      source_external_id=origin.external_id, source_name="Synthetic pilot",
                      content="", summary="", context_evidence="", attachments_json="[]",
                      mail_connection_id=mail.id, provider_message_id=origin.external_id,
                      source_reference_id=origin.id, context_version=1, context_confirmed=False,
                      context_confidence=0, analysis_required=True, status="needs_review")
        db.add(msg)
        db.flush()
        result = self._ref(scope, "message", msg.id)
        self._audit(db, scope, result, "SOURCE_OBSERVED")
        return result

    @staticmethod
    def _cas_message(db, msg, expected, **values):
        if type(expected) is not int or expected <= 0 or msg.context_version != expected:
            raise ContextError("context_version_conflict")
        result = db.execute(update(Message).where(Message.id == msg.id,
            Message.organization_id == msg.organization_id, Message.context_version == expected)
            .values(context_version=expected + 1, **values).execution_options(synchronize_session="fetch"))
        if result.rowcount != 1:
            raise ContextError("context_version_conflict")

    @staticmethod
    def _cas_relation(db, row, expected, **values):
        if row.record_version != expected:
            raise ContextError("relation_version_conflict")
        result = db.execute(update(ContextRelation).where(ContextRelation.id == row.id,
            ContextRelation.organization_id == row.organization_id, ContextRelation.record_version == expected)
            .values(record_version=expected + 1, **values).execution_options(synchronize_session="fetch"))
        if result.rowcount != 1:
            raise ContextError("relation_version_conflict")

    def _assertion(self, db, scope, msg, target, evidence, *, kind, previous=None, relation_id=None):
        row = ContextRelation(id=relation_id or str(uuid4()), organization_id=msg.organization_id,
            message_id=msg.id, lineage_id=previous.lineage_id if previous else str(uuid4()),
            revision=previous.revision + 1 if previous else 1,
            relation_type="communication." + target.ref.type, target_ref=target.ref.model_dump(mode="json"),
            scope_ref=self._ref(scope, "mail_connection", msg.mail_connection_id).model_dump(mode="json"),
            expected_target=target.model_dump(mode="json"), expected_context_version=msg.context_version,
            evidence_pins=[p.model_dump(mode="json") for p in evidence],
            provenance={"kind": kind, "initiated_by": scope.actor.model_dump(mode="json"),
                        "supersedes": self._ref(scope, "context_relation", previous.id).model_dump(mode="json") if previous else None},
            state="hypothesis", applicability="current")
        db.add(row)
        db.flush()
        return row

    @boundary
    def propose(self, db, *, scope, message: ObjectRef, expected_context_version: int,
                project: VersionPin, contract: VersionPin | None,
                evidence: tuple[VersionPin, ...]) -> tuple[VersionPin, ...]:
        msg = self._message(db, scope, message)
        if msg.context_version != expected_context_version or type(expected_context_version) is not int:
            raise ContextError("context_version_conflict")
        if msg.context_confirmed:
            raise ContextError("manual_context_protected")
        self._targets(db, scope, project, contract)
        self._evidence(db, scope, msg, evidence)
        result, created = [], []
        for target in (project, contract):
            if target is None:
                continue
            # Stable across transport retry/model wording, including rejected rows.
            signature = canonical_json({"message": message.model_dump(mode="json"),
                "context_version": expected_context_version, "target": target.model_dump(mode="json"),
                "evidence": [p.model_dump(mode="json") for p in evidence]})
            ident = str(uuid5(UUID("42d36b2b-daa8-4c81-8118-5d6a320d1228"), signature))
            row = self._row(db, ContextRelation, ident)
            if row is None:
                row = self._assertion(db, scope, msg, target, evidence, kind="synthetic_analysis", relation_id=ident)
                created.append(self._ref(scope, "context_relation", row.id))
            result.append(self._pin(self._ref(scope, "context_relation", row.id), row.revision, "revision"))
        if created:
            self._audit(db, scope, message, "CONTEXT_PROPOSED", created)
        return tuple(result)

    def _selected(self, db, scope, msg, command, required_state):
        if msg.context_version != command.expected_context_version:
            raise ContextError("context_version_conflict")
        rows = []
        pairs = [(command.project_relation, command.expected_project_relation_record_version, "communication.project"),
                 (command.contract_relation, command.expected_contract_relation_record_version, "communication.contract")]
        for pin, version, relation_type in pairs:
            if pin is None:
                continue
            self._allow(db, scope, pin)
            row = self._row(db, ContextRelation, pin.ref.id.value)
            if (not row or row.organization_id != msg.organization_id or row.message_id != msg.id
                    or row.relation_type != relation_type or row.revision != pin.value):
                raise ContextError("resource_unavailable")
            if row.record_version != version:
                raise ContextError("relation_version_conflict")
            if row.state != required_state or row.applicability != "current":
                raise ContextError("context_state_conflict")
            if required_state == "hypothesis" and row.expected_context_version != msg.context_version:
                raise ContextError("context_version_conflict")
            self._evidence(db, scope, msg, tuple(VersionPin.model_validate(p) for p in row.evidence_pins))
            rows.append(row)
        return rows

    @boundary
    def confirm(self, db, *, scope, command: ContextConfirmation) -> None:
        command = ContextConfirmation.model_validate(command.model_dump(mode="json"))
        msg = self._message(db, scope, command.message)
        rows = self._selected(db, scope, msg, command, "hypothesis")
        if msg.context_confirmed or db.scalar(select(ContextRelation.id).where(
                ContextRelation.message_id == msg.id, ContextRelation.state == "confirmed",
                ContextRelation.relation_type.in_(["communication.project", "communication.contract"]))):
            raise ContextError("manual_context_protected")
        project = VersionPin.model_validate(rows[0].expected_target)
        contract = VersionPin.model_validate(rows[1].expected_target) if len(rows) == 2 else None
        self._targets(db, scope, project, contract)
        self._cas_message(db, msg, command.expected_context_version,
                          project_id=int(project.ref.id.value), contract_id=int(contract.ref.id.value) if contract else None,
                          context_confirmed=True, analysis_required=False)
        for row in rows:
            self._cas_relation(db, row, row.record_version, state="confirmed",
                               confirmed_by=int(scope.actor.id.value), confirmed_at=self.clock())
        self._audit(db, scope, command.message, "CONTEXT_CONFIRMED",
                    [self._ref(scope, "context_relation", row.id) for row in rows])

    @boundary
    def correct(self, db, *, scope, command: ContextConfirmation, project: VersionPin,
                contract: VersionPin | None, evidence: tuple[VersionPin, ...]) -> tuple[VersionPin, ...]:
        """command pins BOTH old primaries + their CAS; new targets are explicit."""
        command = ContextConfirmation.model_validate(command.model_dump(mode="json"))
        msg = self._message(db, scope, command.message)
        old = self._selected(db, scope, msg, command, "confirmed")
        live = set(db.scalars(select(ContextRelation.id).where(ContextRelation.message_id == msg.id,
            ContextRelation.state == "confirmed",
            ContextRelation.relation_type.in_(["communication.project", "communication.contract"]))))
        if live != {r.id for r in old} or not msg.context_confirmed:
            raise ContextError("both_primary_versions_required")
        self._targets(db, scope, project, contract)
        self._evidence(db, scope, msg, evidence)
        # Atomic invalidation of both old assertions; never carries old contract to a new project.
        for row in old:
            self._cas_relation(db, row, row.record_version, state="superseded")
        new = []
        for target in (project, contract):
            if target is None:
                continue
            previous = next((r for r in old if r.relation_type == "communication." + target.ref.type), None)
            row = self._assertion(db, scope, msg, target, evidence, kind="human_correction", previous=previous)
            self._cas_relation(db, row, row.record_version, state="confirmed",
                               confirmed_by=int(scope.actor.id.value), confirmed_at=self.clock())
            new.append(row)
        self._cas_message(db, msg, command.expected_context_version, project_id=int(project.ref.id.value),
                          contract_id=int(contract.ref.id.value) if contract else None, analysis_required=False)
        self._audit(db, scope, command.message, "CONTEXT_CONFIRMED",
                    [self._ref(scope, "context_relation", r.id) for r in old + new])
        return tuple(self._pin(self._ref(scope, "context_relation", r.id), r.revision, "revision") for r in new)

    @boundary
    def handoff(self, db, *, scope, message: ObjectRef, envelope: ActionEnvelope,
                trust: TrustWriter) -> VersionPin:
        """Preflight only. Trust.freeze MUST lock/recheck before sealing (no inverse locks)."""
        envelope = ActionEnvelope.model_validate(envelope.model_dump(mode="json"))
        msg = self._message(db, scope, message, lock=False)
        if (envelope.action_type != "task.internal.create" or envelope.requested_by != scope.actor
                or envelope.project_ref != scope.project or not msg.context_confirmed
                or envelope.expected_context_version != msg.context_version):
            raise ContextError("context_version_conflict")
        mail = db.get(MailConnection, msg.mail_connection_id)
        if envelope.connection_ref != self._ref(scope, "connection_identity", mail.identity_id):
            raise ContextError("origin_conflict")
        rows = list(db.scalars(select(ContextRelation).where(ContextRelation.message_id == msg.id,
            ContextRelation.state == "confirmed", ContextRelation.applicability == "current",
            ContextRelation.relation_type.in_(["communication.project", "communication.contract"]))))
        expected = {canonical_json(self._pin(self._ref(scope, "context_relation", r.id), r.revision, "revision").model_dump(mode="json")) for r in rows}
        if len(rows) != 2 or expected != {canonical_json(p.model_dump(mode="json")) for p in envelope.relations}:
            raise ContextError("context_state_conflict")
        contract = next(r for r in rows if r.relation_type == "communication.contract")
        project = next(r for r in rows if r.relation_type == "communication.project")
        project_pin = VersionPin.model_validate(project.expected_target)
        contract_pin = VersionPin.model_validate(contract.expected_target)
        self._targets(db, scope, project_pin, contract_pin, lock=False)
        if envelope.target != project_pin or project_pin.ref != scope.project:
            raise ContextError("context_state_conflict")
        if envelope.payload.contract_ref != ObjectRef.model_validate(contract.target_ref):
            raise ContextError("contract_project_mismatch")
        for pin in envelope.relations:
            self._allow(db, scope, pin, lock=False)
        self._evidence(db, scope, msg, envelope.evidence, lock=False)
        context_evidence = {canonical_json(p) for r in rows for p in r.evidence_pins}
        if not context_evidence.issubset({canonical_json(p.model_dump(mode="json")) for p in envelope.evidence}):
            raise ContextError("evidence_origin_mismatch")
        current = db.get(SourceCurrent, msg.source_reference_id, populate_existing=True)
        if not current:
            raise ContextError("resource_unavailable")
        observation_ids = {current.version_id}
        observation_ids.update(db.get(Evidence, p.ref.id.value).source_version_id for p in envelope.evidence)
        if {p.ref.id.value for p in envelope.source_versions} != observation_ids:
            raise ContextError("evidence_origin_mismatch")
        for pin in envelope.source_versions:
            self._allow(db, scope, pin, lock=False)
        self._allow(db, scope, envelope.claim, operation="metadata", lock=False)
        claim = db.get(DeadlineClaim, (envelope.claim.ref.id.value, envelope.claim.value))
        if not claim or claim.organization_id != msg.organization_id or claim.message_id != msg.id:
            raise ContextError("claim_origin_mismatch")
        # C owns claim validation, stable intent dedup, payload sealing and audit.
        # This call never approves or requests dispatch.
        result = trust.freeze(db, scope=scope, envelope=envelope)
        if result != self._pin(envelope.action_ref, envelope.revision, "revision"):
            raise ContextError("trust_handoff_mismatch")
        return result

    @boundary
    def project_receipt(self, db, *, scope, receipt: ObjectRef) -> VersionPin:
        """Consume a persisted C-owned receipt; never accepts a fabricated receipt DTO."""
        require_same_tenant(scope.tenant, receipt)
        if receipt.type != "receipt":
            raise ContextError("resource_unavailable")
        row = self._row(db, ActionReceipt, receipt.id.value, lock=False)
        if not row or row.organization_id != int(scope.tenant.value) or row.outcome != "APPLIED":
            raise ContextError("resource_unavailable")
        action = self._row(db, PilotAction, row.action_id, lock=False)
        if not action or action.organization_id != row.organization_id or action.action_type != "task.internal.create":
            raise ContextError("resource_unavailable")
        self._allow(db, scope, self._pin(self._ref(scope, "action", action.id), row.revision, "revision"),
                    operation="metadata", lock=False)
        msg = self._message(db, scope, self._ref(scope, "message", action.message_id))
        revision = db.get(ActionRevision, (row.action_id, row.revision))
        approval = db.get(ActionApproval, row.approval_id)
        if (not revision or not approval or revision.envelope_hash != row.envelope_hash
                or approval.action_id != row.action_id or approval.revision != row.revision
                or approval.envelope_hash != row.envelope_hash or not row.target_ref):
            raise ContextError("resource_unavailable")
        envelope = ActionEnvelope.model_validate(revision.envelope)
        if canonical_hash(revision.envelope) != row.envelope_hash:
            raise ContextError("resource_unavailable")
        target = ObjectRef.model_validate(row.target_ref)
        require_same_tenant(scope.tenant, target)
        if target.type != "task":
            raise ContextError("resource_unavailable")
        task = self._row(db, Task, int(target.id.value), lock=False)
        if not task or task.message_id != msg.id or task.project_id != action.project_id:
            raise ContextError("resource_unavailable")
        target_pin = self._pin(target, task.record_version)
        self._allow(db, scope, target_pin)
        self._evidence(db, scope, msg, envelope.evidence)
        existing = db.scalar(select(ContextRelation).where(ContextRelation.receipt_id == row.id))
        if existing:
            if existing.message_id != msg.id or existing.target_ref != row.target_ref:
                raise ContextError("resource_unavailable")
            return self._pin(self._ref(scope, "context_relation", existing.id), existing.revision, "revision")
        relation = ContextRelation(organization_id=msg.organization_id, message_id=msg.id,
            lineage_id=str(uuid4()), revision=1, relation_type="communication.task",
            target_ref=row.target_ref, expected_target=target_pin.model_dump(mode="json"),
            scope_ref=envelope.project_ref.model_dump(mode="json"),
            expected_context_version=envelope.expected_context_version,
            evidence_pins=[p.model_dump(mode="json") for p in envelope.evidence],
            provenance={"kind": "receipt_projection", "receipt": receipt.model_dump(mode="json")},
            state="confirmed", applicability="current" if msg.context_version == envelope.expected_context_version else "stale",
            confirmed_by=approval.approver_id, confirmed_at=row.recorded_at, receipt_id=row.id)
        db.add(relation)
        db.flush()
        result = self._pin(self._ref(scope, "context_relation", relation.id), 1, "revision")
        self._audit(db, scope, self._ref(scope, "message", msg.id), "CONTEXT_CONFIRMED", [result.ref], receipt)
        return result

    @boundary
    def analysis_payload(self, db, *, scope, message: ObjectRef) -> dict:
        """ID-only intent for a future wiring owner; does not call BackgroundJob."""
        msg = self._message(db, scope, message)
        return {"message_ref": message.model_dump(mode="json"),
                "expected_context_version": msg.context_version, "correlation_id": scope.correlation_id}
