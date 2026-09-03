"""Explicitly injected synthetic composition. No HTTP/config-driven enable path.

Source policies are test authority snapshots, NOT production ACL/retention.
Every participant locks the existing intake Project first. Distributed durable
authority and legacy writer cutover remain rollout gates, not inferred grants.
"""
from sqlalchemy import func, select

from app.action_trust.facade import TrustFacade
from app.action_trust.guards import Guards
from app.context_communication.service import ContextCommunication
from app.core.v54_dto import canonical_hash
from app.core.v54_interfaces import PilotGate, Resolution
from app.core.v54_permissions import deny, load, utc, utcnow
from app.core.v54_refs import require_same_tenant
from app.models.ai_secretary import Message
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.models.v54_pilot import (ActionPolicy, ActionRevision, ConnectionIdentity,
    ContextRelation, DeadlineClaim, MailConnection, PilotAction, SourceReference)
from app.pilot_task_mutation import InternalTaskMutation
from app.source_evidence.facade import SourceEvidenceFacade
from app.task_claims import DeadlineClaims


class SyntheticResolver:
    """Real source facade for A pins; explicit DB checks for B/C domain pins.

    This is a synthetic composition adapter, never a replacement for real ACL.
    Unknown types/operations/versions deny rather than returning fabricated pins.
    """
    def __init__(self, policy, clock=utcnow):
        self.policy, self.clock = policy, clock
        self.source = SourceEvidenceFacade(policy, clock)

    def resolve(self, db, *, scope, pin, operation, lock):
        expires = self.policy.require(db, scope, operation, self.clock(), lock=True)
        require_same_tenant(scope.tenant, pin.ref)
        if operation == "fragment":
            deny()
        if pin.ref.type in {"source", "source_version", "evidence"}:
            return self.source.resolve(db, scope=scope, pin=pin, operation=operation, lock=lock)
        kind, key = pin.ref.type, pin.ref.id.value
        verified = "unverified"
        epoch = self.policy.resolved_authority_epoch(db, scope, operation, self.clock(), lock=lock)
        if kind == "project":
            row = load(db, Project, Project.id == int(key), lock=lock)
            if not row or row.id != self.policy.project_id or row.organization_id != self.policy.tenant_id or row.archived_at:
                deny()
            version, version_kind = row.record_version, "record_version"
        elif kind == "contract":
            row = load(db, Contract, Contract.id == int(key), Contract.project_id == self.policy.project_id, lock=lock)
            if not row:
                deny()
            version, version_kind = row.record_version, "record_version"
        elif kind in {"connection_identity", "mail_connection"}:
            if kind == "mail_connection":
                row = load(db, MailConnection, MailConnection.id == key,
                           MailConnection.organization_id == self.policy.tenant_id, lock=lock)
                if not row or row.state != "active":
                    deny()
                identity = load(db, ConnectionIdentity, ConnectionIdentity.id == row.identity_id, lock=lock)
                self.policy.identity(identity, row.namespace)
            else:
                row = identity = load(db, ConnectionIdentity, ConnectionIdentity.id == key, lock=lock)
                self.policy.identity(identity)
            epoch = identity.binding_epoch
            version, version_kind = row.record_version, "record_version"
        elif kind == "policy":
            row = load(db, ActionPolicy, ActionPolicy.id == key, ActionPolicy.revision == pin.value,
                       ActionPolicy.organization_id == self.policy.tenant_id, lock=lock)
            latest = db.scalar(select(func.max(ActionPolicy.revision)).where(ActionPolicy.id == key))
            if (not row or pin != self.policy.pin or latest != pin.value or row.scope_ref != scope.project.model_dump(mode="json")
                    or row.policy_hash != canonical_hash(row.rules) or row.rules.get("synthetic_only") is not True
                    or row.mode != "CONFIRM" or utc(row.valid_until) <= self.clock()):
                deny()
            expires = min(expires, utc(row.valid_until))
            version, version_kind = row.revision, "revision"
        elif kind in {"message", "deadline_claim", "context_relation", "action", "task"}:
            if kind == "message":
                row = load(db, Message, Message.id == int(key), lock=lock)
                message = row
                version, version_kind = (row.context_version if row else None), "record_version"
            else:
                model = {"deadline_claim": DeadlineClaim, "context_relation": ContextRelation,
                         "action": PilotAction, "task": Task}[kind]
                clauses = [model.id == (int(key) if kind == "task" else key)]
                if kind == "deadline_claim":
                    clauses.append(DeadlineClaim.revision == pin.value)
                row = load(db, model, *clauses, lock=lock)
                message = load(db, Message, Message.id == row.message_id, lock=lock) if row else None
                if kind == "action":
                    sealed = db.get(ActionRevision, (key, pin.value))
                    if not sealed or sealed.organization_id != self.policy.tenant_id:
                        deny()
                    version, version_kind = sealed.revision, "revision"
                elif kind == "task":
                    if not row or row.source_type != "v54_synthetic" or row.project_id != self.policy.project_id:
                        deny()
                    version, version_kind = row.record_version, "record_version"
                else:
                    version, version_kind = (row.revision if row else None), "revision"
                    if kind == "deadline_claim" and row:
                        latest = db.scalar(select(func.max(DeadlineClaim.revision)).where(DeadlineClaim.id == key))
                        if latest != row.revision:
                            deny()
                        verified = "verified" if row.verification == "confirmed" else "unverified"
            if (not row or not message or message.organization_id != self.policy.tenant_id
                    or message.project_id != self.policy.project_id or message.source_type != "synthetic"
                    or not message.mail_connection_id or not message.source_reference_id):
                deny()
            mail = load(db, MailConnection, MailConnection.id == message.mail_connection_id, lock=lock)
            if not mail or mail.organization_id != self.policy.tenant_id or mail.state != "active":
                deny()
            identity = load(db, ConnectionIdentity, ConnectionIdentity.id == mail.identity_id, lock=lock)
            self.policy.identity(identity, mail.namespace)
            source = load(db, SourceReference, SourceReference.id == message.source_reference_id, lock=lock)
            if (not source or source.organization_id != self.policy.tenant_id or source.origin_project_id != self.policy.project_id
                    or source.identity_id != identity.id or source.namespace != mail.namespace or source.object_kind != "message"
                    or source.external_id != message.provider_message_id or source.external_id != message.source_external_id):
                deny()
            epoch = identity.binding_epoch
        else:
            deny()
        if pin.value != version or pin.version_kind != version_kind:
            deny()
        return Resolution(pin=pin, actor=scope.actor, project=scope.project, operation=operation,
            acl="allow", version="current", freshness="fresh", availability="available", verification=verified,
            policy_known=True, retention_known=self.policy.retention_known, residency_allowed=self.policy.residency_allowed,
            valid_until=expires,
            authority_epoch=self.policy.resolved_authority_epoch(db, scope, operation, self.clock(), lock=lock),
            binding_epoch=epoch)


class _OrderedContext(ContextCommunication):
    def _entry(self, db, scope):
        self.resolver.policy.require(db, scope, "review", self.clock(), lock=True)
        super()._entry(db, scope)


class SyntheticComposition:
    def __init__(self, *, policy, clock=utcnow, enabled=False):
        self.policy, self.clock, self.enabled = policy, clock, enabled
        self.resolver = SyntheticResolver(policy, clock)
        self.source = self.resolver.source
        self.guards = Guards(resolver=self.resolver, authorize=self.authorize, gate=self.gate, clock=clock)
        self.trust = TrustFacade(guards=self.guards)
        self.claims = DeadlineClaims(guards=self.guards)
        self.mutation = InternalTaskMutation(guards=self.guards)

    def authorize(self, db, scope, operation, subject, *, lock):
        require_same_tenant(scope.tenant, subject)
        self.policy.require(db, scope, operation, self.clock(), lock=True)
        if self.policy.authority is not None:
            return self.policy.authority.authorize_subject(db, scope, operation, subject, lock=True)
        if operation == "task.assign":
            if (subject.type != "user" or (int(subject.id.value), "task.assignee") not in self.policy.grants
                    or db.get(User, int(subject.id.value)) is None):
                deny()
        return True

    def gate(self, db, scope):
        return PilotGate(synthetic_scope_authorized=self.enabled is True and self.policy.synthetic_only,
            roles_known=self.policy.acl == "allow" and self.policy.authority is not None,
            retention_known=self.policy.retention_known,
            valid_until=self.policy.valid_until)

    def context(self, db, scope):
        return _OrderedContext(resolver=self.resolver, gate=self.gate(db, scope), clock=self.clock,
            authorize_audit=self.guards.audit_allowed, authorize_mailbox=self.authorize_mailbox)

    def authorize_mailbox(self, db, scope, identity, namespace):
        self.policy.require(db, scope, "mailbox.bootstrap", self.clock(), lock=True)
        row = load(db, ConnectionIdentity, ConnectionIdentity.id == identity.id.value, lock=True)
        self.policy.identity(row, namespace)
        return True
