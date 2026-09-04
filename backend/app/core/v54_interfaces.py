"""Foundation contracts only: no endpoint, executor, enqueue or policy defaults.

All mutation Protocol methods join the caller's Session transaction. They may
flush but MUST NOT commit/rollback/close, enqueue, or perform provider I/O.
The wiring owner commits T1 before queue.enqueue in a DIFFERENT session; T2
joins Task + history + receipt + AuditLog. See the handoff for lock ordering.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import AwareDatetime, StrictBool, StrictInt, StrictStr, model_validator
from sqlalchemy.orm import Session

from app.core.v54_dto import ActionEnvelope
from app.core.v54_refs import ObjectRef, StrictDTO, TaggedId, VersionPin, require_same_tenant


class RequestScope(StrictDTO):
    """Construct only from authenticated server context, never from model/API body."""
    actor: ObjectRef
    tenant: TaggedId
    project: ObjectRef
    correlation_id: StrictStr

    @model_validator(mode="after")
    def validate_scope(self):
        require_same_tenant(self.tenant, self.actor, self.project)
        if self.actor.type != "user" or self.project.type != "project" or not self.correlation_id:
            raise ValueError("invalid request scope")
        return self


class Resolution(StrictDTO):
    """Authoritative resolver output; booleans require explicit server evidence."""
    pin: VersionPin
    actor: ObjectRef
    project: ObjectRef
    operation: Literal["metadata", "fragment", "review", "dispatch"]
    acl: Literal["allow", "deny", "unknown"] = "unknown"
    version: Literal["current", "historical", "changed", "unknown"] = "unknown"
    freshness: Literal["fresh", "stale", "unknown"] = "unknown"
    availability: Literal["available", "unavailable", "unknown"] = "unknown"
    verification: Literal["verified", "unverified"] = "unverified"
    policy_known: StrictBool = False
    retention_known: StrictBool = False
    residency_allowed: StrictBool = False
    valid_until: AwareDatetime | None = None
    authority_epoch: StrictInt | None = None
    binding_epoch: StrictInt | None = None


class Resolver(Protocol):
    def resolve(self, db: Session, *, scope: RequestScope, pin: VersionPin,
                operation: str, lock: bool) -> Resolution:
        """Filter ACL before lookup output; lock=True joins T2 authority protocol."""
        ...


def require_resolution(result: Resolution, *, scope: RequestScope, pin: VersionPin,
                       operation: str, now: datetime) -> None:
    """No admin/unknown/legacy-null fallback; does not implement an ACL backend."""
    require_same_tenant(scope.tenant, pin.ref, result.pin.ref)
    version_allowed = result.version == "current" or (
        operation == "fragment" and result.version == "historical"
    )
    if (now.tzinfo is None or result.pin != pin or result.actor != scope.actor
            or result.project != scope.project or result.operation != operation
            or result.acl != "allow" or not version_allowed
            or result.freshness != "fresh" or result.availability != "available"
            or not result.policy_known or not result.retention_known or not result.residency_allowed
            or result.valid_until is None or result.valid_until <= now
            or result.authority_epoch is None or result.authority_epoch <= 0
            or result.binding_epoch is None or result.binding_epoch <= 0
            or (operation == "dispatch" and pin.ref.type in {"evidence", "deadline_claim"}
                and result.verification != "verified")):
        raise ValueError("resource_unavailable")


class PilotGate(StrictDTO):
    """No enabled production defaults. Tests pass an explicitly synthetic grant."""
    synthetic_scope_authorized: StrictBool = False
    roles_known: StrictBool = False
    retention_known: StrictBool = False
    valid_until: AwareDatetime | None = None

    def require_confirm(self, *, mode: str, action_type: str, now: datetime) -> None:
        if (mode != "CONFIRM" or action_type not in {"task.internal.create", "task.internal.cancel"}
                or not self.synthetic_scope_authorized or not self.roles_known or not self.retention_known
                or now.tzinfo is None or self.valid_until is None or self.valid_until <= now):
            raise ValueError("pilot_disabled")


class ContextConfirmation(StrictDTO):
    message: ObjectRef
    project_relation: VersionPin
    contract_relation: VersionPin | None = None
    expected_context_version: StrictInt
    expected_project_relation_record_version: StrictInt
    expected_contract_relation_record_version: StrictInt | None = None

    @model_validator(mode="after")
    def validate_confirmation(self):
        refs = [self.project_relation.ref]
        if self.contract_relation:
            refs.append(self.contract_relation.ref)
        require_same_tenant(self.message.tenant_id, *refs)
        if (self.message.type != "message" or any(r.type != "context_relation" for r in refs)
                or self.project_relation.version_kind != "revision"
                or (self.contract_relation and self.contract_relation.version_kind != "revision")
                or self.expected_context_version <= 0 or self.expected_project_relation_record_version <= 0
                or (self.contract_relation is not None) != (self.expected_contract_relation_record_version is not None)
                or (self.expected_contract_relation_record_version is not None and self.expected_contract_relation_record_version <= 0)):
            raise ValueError("invalid context confirmation")
        return self


class DispatchBinding(StrictDTO):
    action: VersionPin
    approval: ObjectRef
    envelope_hash: StrictStr
    command_key: StrictStr
    job: ObjectRef
    # Existing queue ownership contract is worker_id + attempts + locked_at;
    # no invented lease-token column or second queue contract.
    worker_id: StrictStr
    job_attempt: StrictInt
    locked_at: AwareDatetime

    @model_validator(mode="after")
    def validate_binding(self):
        import re
        require_same_tenant(self.action.ref.tenant_id, self.approval, self.job)
        if (self.action.ref.type != "action" or self.action.version_kind != "revision"
                or self.approval.type != "approval" or self.job.type != "background_job"
                or not re.fullmatch("[0-9a-f]{64}", self.envelope_hash)
                or not self.command_key or len(self.command_key) > 200
                or not self.worker_id or self.job_attempt <= 0):
            raise ValueError("invalid dispatch binding")
        return self


class ContextWriter(Protocol):
    def confirm(self, db: Session, *, scope: RequestScope, command: ContextConfirmation) -> None: ...


class ReviewCommand(StrictDTO):
    subject: VersionPin
    expected_record_version: StrictInt
    decision: Literal["confirmed", "rejected"]

    @model_validator(mode="after")
    def validate_review(self):
        if (self.subject.ref.type not in {"deadline_claim", "evidence"}
                or self.subject.version_kind != "revision" or self.expected_record_version <= 0):
            raise ValueError("invalid review command")
        return self


class AssessmentWriter(Protocol):
    def review(self, db: Session, *, scope: RequestScope, command: ReviewCommand) -> None:
        """Evidence or Task-claim owner; reviewer from scope, no Task side effect."""
        ...


class TrustWriter(Protocol):
    def freeze(self, db: Session, *, scope: RequestScope, envelope: ActionEnvelope) -> VersionPin: ...

    def approve(self, db: Session, *, scope: RequestScope, action: VersionPin,
                envelope_hash: str, command_key: str, expires_at: datetime) -> ObjectRef:
        """Human scope + owner policy; no default role, expiry or self-approval."""
        ...

    def request_dispatch(self, db: Session, *, scope: RequestScope, action: VersionPin,
                         approval: ObjectRef, expected_record_version: int) -> None:
        """T1 persists PendingDispatch only. Caller commits BEFORE queue enqueue."""
        ...


class TaskMutation(Protocol):
    def apply(self, db: Session, *, scope: RequestScope, binding: DispatchBinding) -> ObjectRef:
        """DB-only Task/TaskHistory; T2 owner supplies locked, authorized binding."""
        ...


class AuditAppend(StrictDTO):
    subject: ObjectRef
    subject_pin: VersionPin | None = None
    sequence: StrictInt
    event: Literal["SOURCE_OBSERVED", "EVIDENCE_REVIEWED", "CONTEXT_CONFIRMED", "CLAIM_REVIEWED",
                   "ACTION_FROZEN", "APPROVAL_GRANTED", "APPROVAL_REVOKED", "DISPATCH_REQUESTED",
                   "DISPATCH_AUTHORIZED", "ACTION_SUCCEEDED", "BLOCKED", "UNKNOWN", "RETENTION_APPLIED",
                   "CLAIM_EXTRACTED", "CLAIM_CONFIRMED", "CLAIM_REJECTED", "CONTEXT_PROPOSED",
                   "AUTHORITY_CHANGED", "MATERIALIZATION_ADMITTED", "MATERIALIZATION_WRITING",
                   "MATERIALIZATION_SEALED", "MATERIALIZATION_DERIVED", "MATERIALIZATION_EXPIRED",
                   "MATERIALIZATION_PURGED", "AUTONOMY_POLICY_CHANGED",
                   "AUTONOMY_POLICY_REVOKED"]
    action: VersionPin | None = None
    approval: ObjectRef | None = None
    receipt: ObjectRef | None = None
    job: ObjectRef | None = None
    relations: tuple[ObjectRef, ...] = ()

    @model_validator(mode="after")
    def validate_event(self):
        if self.sequence <= 0:
            raise ValueError("invalid audit sequence")
        if self.subject_pin and self.subject_pin.ref != self.subject:
            raise ValueError("audit subject pin mismatch")
        for ref, kind in [(self.approval, "approval"), (self.receipt, "receipt"), (self.job, "background_job")]:
            if ref:
                require_same_tenant(self.subject.tenant_id, ref)
                if ref.type != kind:
                    raise ValueError("invalid audit reference")
        for ref in self.relations:
            require_same_tenant(self.subject.tenant_id, ref)
            if ref.type != "context_relation":
                raise ValueError("invalid relation reference")
        if self.action:
            require_same_tenant(self.subject.tenant_id, self.action.ref)
            if self.action.ref.type != "action" or self.action.version_kind != "revision":
                raise ValueError("invalid action pin")
        return self


class AuditWriter(Protocol):
    def append(self, db: Session, *, scope: RequestScope, event: AuditAppend) -> ObjectRef:
        """Single writer adds AuditLog + extension, no content/details, no commit."""
        ...
