"""Caller-transaction CONFIRM trust facade, deliberately not wired to workers."""
from datetime import datetime

from sqlalchemy import func, select

from app.action_trust.guards import Guards, TrustConflict, reference, revision, sequence, utc
from app.action_trust.validation import live_pins
from app.action_trust.state import require_new_effect
from app.core import v54_transactions
from app.core.v54_dto import ActionEnvelope, canonical_hash
from app.core.v54_interfaces import AuditAppend, DispatchBinding, RequestScope, TaskMutation
from app.core.v54_refs import ObjectRef, VersionPin, require_same_tenant
from app.models.ai_secretary import Message
from app.models.job import BackgroundJob
from app.models.task import Task
from app.models.v54_pilot import (
    ActionApproval, ActionPolicy, ActionReceipt, ActionRevision, DeadlineClaim,
    PendingDispatch, PilotAction,
)


class TrustFacade:
    def __init__(self, *, guards: Guards):
        self.guards = guards

    def _policy(self, db, scope, e, *, live=True):
        policy = db.scalar(select(ActionPolicy).where(ActionPolicy.id == e.policy.ref.id.value,
            ActionPolicy.revision == e.policy.value, ActionPolicy.organization_id == int(scope.tenant.value))
            .with_for_update().execution_options(populate_existing=True))
        if policy is None:
            raise TrustConflict("policy_unavailable")
        if live:
            latest = db.scalar(select(func.max(ActionPolicy.revision)).where(
                ActionPolicy.id == policy.id, ActionPolicy.organization_id == policy.organization_id))
            if (latest != policy.revision or policy.mode != "CONFIRM" or utc(policy.valid_until) <= self.guards.now()
                    or policy.policy_hash != e.policy_sha256 or canonical_hash(policy.rules) != e.policy_sha256
                    or ObjectRef.model_validate(policy.scope_ref) != scope.project
                    or policy.rules.get("synthetic_only") is not True
                    or policy.rules.get("auto_enabled") is not False
                    or policy.rules.get("external_execute") is not False
                    or e.action_type not in policy.rules.get("allowed_action_types", [])):
                raise TrustConflict("policy_unavailable")
            self.guards.resolve(db, scope, e.policy, "review")
        return policy

    def _load(self, db, scope, pin, *, operation, live_policy=True):
        if pin.ref.type != "action" or pin.version_kind != "revision":
            raise TrustConflict("invalid_action_pin")
        self.guards.allow(db, scope, operation, pin.ref)
        row = db.scalar(select(ActionRevision).where(ActionRevision.action_id == pin.ref.id.value,
            ActionRevision.revision == pin.value, ActionRevision.organization_id == int(scope.tenant.value))
            .execution_options(populate_existing=True))
        if row is None:
            raise TrustConflict("resource_unavailable")
        e = ActionEnvelope.model_validate(row.envelope)
        if canonical_hash(e.model_dump(mode="json")) != row.envelope_hash:
            raise TrustConflict("seal_mismatch")
        # Immutable revision peek above, authority/policy then action locks.
        self._policy(db, scope, e, live=live_policy)
        action = db.scalar(select(PilotAction).where(PilotAction.id == row.action_id,
            PilotAction.organization_id == int(scope.tenant.value)).with_for_update()
            .execution_options(populate_existing=True))
        if action is None or action.project_id != int(scope.project.id.value) or e.project_ref != scope.project:
            raise TrustConflict("resource_unavailable")
        return action, row, e

    def _event(self, db, scope, action_pin, event, *, approval=None, receipt=None, job=None):
        # Only constructs the common DTO. append_audit below is the sole writer.
        return AuditAppend(subject=action_pin.ref, subject_pin=action_pin, action=action_pin,
                           sequence=sequence(db, action_pin.ref), event=event,
                           approval=approval, receipt=receipt, job=job)

    def freeze(self, db, *, scope: RequestScope, envelope: ActionEnvelope) -> VersionPin:
        e = ActionEnvelope.model_validate(envelope.model_dump(mode="json"))
        self.guards.allow(db, scope, "action.freeze", e.action_ref)
        self.guards.enabled(db, scope, e.action_type)
        if e.requested_by != scope.actor or e.project_ref != scope.project:
            raise TrustConflict("requester_mismatch")
        self._policy(db, scope, e)
        # Serialize stable identity even before a PilotAction row exists.
        claim_message = db.scalar(select(DeadlineClaim.message_id).where(
            DeadlineClaim.id == e.claim.ref.id.value, DeadlineClaim.revision == e.claim.value,
            DeadlineClaim.organization_id == int(scope.tenant.value)))
        action = db.scalar(select(PilotAction).where(PilotAction.id == e.action_ref.id.value)
                           .with_for_update().execution_options(populate_existing=True))
        message = db.scalar(select(Message).where(Message.id == claim_message,
            Message.organization_id == int(scope.tenant.value), Message.project_id == int(scope.project.id.value))
            .with_for_update().execution_options(populate_existing=True))
        if message is None:
            raise TrustConflict("resource_unavailable")
        intent = db.scalar(select(PilotAction).where(PilotAction.organization_id == int(scope.tenant.value),
            PilotAction.message_id == message.id, PilotAction.claim_id == e.claim.ref.id.value,
            PilotAction.action_type == e.action_type))
        if intent is not None and intent.id != e.action_ref.id.value:
            raise TrustConflict("business_identity_conflict")
        compensates = e.compensates_action_ref.id.value if e.compensates_action_ref else None
        if action is not None and (action.organization_id != int(scope.tenant.value)
                or action.project_id != int(scope.project.id.value) or action.message_id != message.id
                or action.claim_id != e.claim.ref.id.value or action.action_type != e.action_type
                or action.compensates_action_id != compensates):
            raise TrustConflict("business_identity_conflict")
        sealed = e.model_dump(mode="json")
        digest = canonical_hash(sealed)
        pin = revision(e.action_ref, e.revision)
        existing = db.get(ActionRevision, (e.action_ref.id.value, e.revision), populate_existing=True)
        if existing:
            if existing.organization_id != int(scope.tenant.value) or existing.envelope_hash != digest:
                raise TrustConflict("action_revision_conflict")
            return pin
        command = db.scalar(select(ActionRevision).where(ActionRevision.organization_id == int(scope.tenant.value),
                                                         ActionRevision.command_key == e.idempotency_key))
        if command is not None:
            raise TrustConflict("command_conflict")
        if action is not None and action.business_state in {
                "EXECUTING", "UNKNOWN", "SUCCEEDED", "CANCELLED", "FAILED_NOT_APPLIED"}:
            raise TrustConflict("action_not_revisable")
        latest = db.scalar(select(func.max(ActionRevision.revision)).where(ActionRevision.action_id == e.action_ref.id.value)) or 0
        if e.revision != latest + 1:
            raise TrustConflict("action_revision_conflict")
        if action is None:
            action = PilotAction(id=e.action_ref.id.value, organization_id=int(scope.tenant.value),
                project_id=int(scope.project.id.value), message_id=message.id, claim_id=e.claim.ref.id.value,
                action_type=e.action_type, compensates_action_id=compensates, record_version=1,
                reservation_fence=0, business_state="AWAITING_APPROVAL")
            db.add(action)
            db.flush()
        live_pins(db, guards=self.guards, scope=scope, envelope=e, action=action, operation="review")
        for old in db.scalars(select(ActionApproval).where(ActionApproval.action_id == action.id,
            ActionApproval.state == "GRANTED").with_for_update()):
            old.state = "INVALIDATED"
            v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
                event=self._event(db, scope, revision(e.action_ref, old.revision), "BLOCKED",
                                  approval=reference(scope, "approval", old.id)))
        pending = db.get(PendingDispatch, action.id)
        if pending:
            pending.pending = False
        action.current_revision, action.business_state = e.revision, "AWAITING_APPROVAL"
        action.record_version += 1
        db.add(ActionRevision(action_id=action.id, revision=e.revision, organization_id=action.organization_id,
            claim_id=action.claim_id, claim_revision=e.claim.value, policy_id=e.policy.ref.id.value,
            policy_revision=e.policy.value, envelope=sealed, envelope_hash=digest,
            command_key=e.idempotency_key, requested_by=int(scope.actor.id.value), created_at=self.guards.now()))
        db.flush()
        v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
            event=self._event(db, scope, pin, "ACTION_FROZEN"))
        return pin

    def approve(self, db, *, scope: RequestScope, action: VersionPin, envelope_hash: str,
                command_key: str, expires_at) -> ObjectRef:
        obj, row, e = self._load(db, scope, action, operation="action.approve")
        self.guards.enabled(db, scope, e.action_type)
        if (not command_key or len(command_key) > 200 or row.envelope_hash != envelope_hash
                or obj.current_revision != action.value
                or obj.business_state not in {"AWAITING_APPROVAL", "READY", "BLOCKED"}
                or not isinstance(expires_at, datetime) or expires_at.tzinfo is None
                or expires_at <= self.guards.now()):
            raise TrustConflict("approval_binding_invalid")
        # Human authority and self-approval decisions belong to injected authorize;
        # here resolve pins and capture the exact live authority epoch, not a role guess.
        result = self.guards.resolve(db, scope, action, "review")
        policy = self._policy(db, scope, e)
        if expires_at > min(utc(policy.valid_until), result.valid_until, self.guards.gate(db, scope).valid_until):
            raise TrustConflict("approval_expiry_out_of_bounds")
        live_pins(db, guards=self.guards, scope=scope, envelope=e, action=obj, operation="review")
        existing = db.scalar(select(ActionApproval).where(ActionApproval.organization_id == obj.organization_id,
                                                           ActionApproval.command_key == command_key))
        if existing:
            if (existing.action_id != obj.id or existing.revision != action.value
                    or existing.envelope_hash != envelope_hash or existing.approver_id != int(scope.actor.id.value)
                    or utc(existing.expires_at) != expires_at or existing.state != "GRANTED"
                    or existing.authority_epoch != result.authority_epoch):
                raise TrustConflict("approval_command_conflict")
            return reference(scope, "approval", existing.id)
        grant = ActionApproval(organization_id=obj.organization_id, action_id=obj.id, revision=row.revision,
            envelope_hash=envelope_hash, command_key=command_key, approver_id=int(scope.actor.id.value),
            authority_epoch=result.authority_epoch, state="GRANTED", granted_at=self.guards.now(), expires_at=expires_at)
        db.add(grant)
        obj.business_state = "READY"
        obj.record_version += 1
        db.flush()
        grant_ref = reference(scope, "approval", grant.id)
        v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
            event=self._event(db, scope, action, "APPROVAL_GRANTED", approval=grant_ref))
        return grant_ref

    def _grant(self, db, scope, obj, row, pin, approval):
        require_same_tenant(scope.tenant, approval)
        if approval.type != "approval":
            raise TrustConflict("approval_binding_invalid")
        grant = db.scalar(select(ActionApproval).where(ActionApproval.id == approval.id.value,
            ActionApproval.organization_id == obj.organization_id).with_for_update()
            .execution_options(populate_existing=True))
        if (grant is None or grant.action_id != obj.id or grant.revision != row.revision
                or grant.envelope_hash != row.envelope_hash or grant.state != "GRANTED"
                or utc(grant.expires_at) <= self.guards.now() or obj.current_revision != row.revision):
            raise TrustConflict("approval_not_applicable")
        reviewer = scope.model_copy(update={"actor": reference(scope, "user", grant.approver_id)})
        self.guards.allow(db, reviewer, "action.approve", pin.ref)
        live = self.guards.resolve(db, reviewer, pin, "review")
        if live.authority_epoch != grant.authority_epoch:
            raise TrustConflict("approval_authority_changed")
        if utc(grant.expires_at) <= self.guards.now():
            raise TrustConflict("approval_not_applicable")
        return grant

    def revoke(self, db, *, scope: RequestScope, approval: ObjectRef) -> None:
        self.guards.allow(db, scope, "action.revoke", approval)
        grant = db.scalar(select(ActionApproval).where(ActionApproval.id == approval.id.value,
            ActionApproval.organization_id == int(scope.tenant.value)))
        if grant is None or approval.type != "approval":
            raise TrustConflict("resource_unavailable")
        pin = revision(reference(scope, "action", grant.action_id), grant.revision)
        obj, row, e = self._load(db, scope, pin, operation="action.revoke", live_policy=False)
        db.refresh(grant, with_for_update=True)
        if grant.state == "REVOKED":
            return
        grant.state = "REVOKED"  # Never reactivates even after freshness refresh.
        pending = db.get(PendingDispatch, obj.id)
        if pending and pending.approval_id == grant.id:
            pending.pending = False
        if obj.current_revision == row.revision and obj.business_state in {"READY", "AWAITING_APPROVAL"}:
            obj.business_state = "BLOCKED"
        obj.record_version += 1
        db.flush()
        v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
            event=self._event(db, scope, pin, "APPROVAL_REVOKED", approval=approval))

    def request_dispatch(self, db, *, scope: RequestScope, action: VersionPin,
                         approval: ObjectRef, expected_record_version: int) -> None:
        obj, row, e = self._load(db, scope, action, operation="action.dispatch")
        self.guards.enabled(db, scope, e.action_type)
        if e.requested_by != scope.actor:
            raise TrustConflict("requester_mismatch")
        self._grant(db, scope, obj, row, action, approval)
        if obj.business_state != "READY":
            raise TrustConflict("action_not_ready")
        live_pins(db, guards=self.guards, scope=scope, envelope=e, action=obj, operation="dispatch")
        pending = db.get(PendingDispatch, obj.id)
        if pending and pending.pending and (pending.revision, pending.envelope_hash, pending.approval_id) == (
                row.revision, row.envelope_hash, approval.id.value):
            return
        if type(expected_record_version) is not int or obj.record_version != expected_record_version:
            raise TrustConflict("action_version_conflict")
        if pending is None:
            pending = PendingDispatch(action_id=obj.id, organization_id=obj.organization_id)
            db.add(pending)
        pending.revision, pending.envelope_hash, pending.approval_id = row.revision, row.envelope_hash, approval.id.value
        pending.pending, pending.job_id = True, None
        obj.record_version += 1
        db.flush()
        v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
            event=self._event(db, scope, action, "DISPATCH_REQUESTED", approval=approval))

    def execute(self, db, *, scope: RequestScope, binding: DispatchBinding,
                mutation: TaskMutation) -> ObjectRef:
        binding = DispatchBinding.model_validate(binding.model_dump(mode="json"))
        obj, row, e = self._load(db, scope, binding.action, operation="action.receipt.read", live_policy=False)
        if (row.envelope_hash != binding.envelope_hash or row.command_key != binding.command_key
                or e.requested_by != scope.actor):
            raise TrustConflict("dispatch_binding_mismatch")
        receipt = db.scalar(select(ActionReceipt).where(ActionReceipt.action_id == obj.id,
                                                        ActionReceipt.organization_id == obj.organization_id))
        if receipt:
            if (receipt.outcome != "APPLIED" or obj.business_state != "SUCCEEDED"
                    or receipt.revision != row.revision or receipt.envelope_hash != row.envelope_hash
                    or receipt.approval_id != binding.approval.id.value):
                raise TrustConflict("outcome_not_replayable")
            # Authorized history read, not a new dispatch: expiry/target change or
            # completed/reclaimed job cannot undo or duplicate an existing effect.
            return reference(scope, "receipt", receipt.id)
        require_new_effect(obj.business_state)
        self.guards.allow(db, scope, "action.execute", binding.action.ref)
        self.guards.enabled(db, scope, e.action_type)
        self._policy(db, scope, e)
        self._grant(db, scope, obj, row, binding.action, binding.approval)
        pending = db.get(PendingDispatch, obj.id, populate_existing=True)
        if (obj.business_state != "READY" or pending is None
                or pending.organization_id != obj.organization_id
                or (pending.revision, pending.envelope_hash, pending.approval_id) != (
                    row.revision, row.envelope_hash, binding.approval.id.value)
                or pending.job_id != int(binding.job.id.value)):
            raise TrustConflict("dispatch_binding_mismatch")
        live_pins(db, guards=self.guards, scope=scope, envelope=e, action=obj, operation="dispatch")
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == int(binding.job.id.value))
                        .with_for_update().execution_options(populate_existing=True))
        self._job(job, binding)
        obj.reservation_fence += 1
        obj.business_state = "EXECUTING"
        obj.record_version += 1
        db.flush()
        v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
            event=self._event(db, scope, binding.action, "DISPATCH_AUTHORIZED", approval=binding.approval, job=binding.job))
        # Locks protect versions, not the passage of time. Pin resolution/audit
        # may outlive the grant: reject BEFORE calling the domain mutation.
        self.guards.enabled(db, scope, e.action_type)
        self._policy(db, scope, e)
        self._grant(db, scope, obj, row, binding.action, binding.approval)
        self._job(job, binding)
        transaction = db.get_transaction()
        target = mutation.apply(db, scope=scope, binding=binding)
        if db.get_transaction() is not transaction or not transaction.is_active:
            raise TrustConflict("task_mutation_transaction_violation")
        require_same_tenant(scope.tenant, target)
        if target.type != "task" or (e.action_type == "task.internal.cancel" and target != e.target.ref):
            raise TrustConflict("mutation_result_invalid")
        task = db.get(Task, int(target.id.value))
        if task is None or task.project_id != obj.project_id:
            raise TrustConflict("mutation_result_invalid")
        if e.action_type == "task.internal.create":
            if (task.status != "assigned" or task.title != e.payload.title
                    or task.due_date is None or task.due_date.isoformat() != e.payload.due_date
                    or task.assignee_user_id != int(e.payload.assignee_ref.id.value)):
                raise TrustConflict("mutation_result_invalid")
        elif task.status != "cancelled" or task.record_version != e.target.value + 1:
            raise TrustConflict("mutation_result_invalid")
        # Mutation must not commit/rollback/close; caller aborts the whole T2 on
        # any failure. Recheck time/lease after DB-only work, before receipt flush.
        self._job(job, binding)
        self._grant(db, scope, obj, row, binding.action, binding.approval)
        receipt = ActionReceipt(organization_id=obj.organization_id, action_id=obj.id, revision=row.revision,
            envelope_hash=row.envelope_hash, approval_id=binding.approval.id.value, job_id=job.id,
            fence=obj.reservation_fence, outcome="APPLIED", target_ref=target.model_dump(mode="json"),
            recorded_at=self.guards.now())
        db.add(receipt)
        obj.business_state = "SUCCEEDED"
        obj.record_version += 1
        pending.pending = False
        db.flush()
        receipt_ref = reference(scope, "receipt", receipt.id)
        v54_transactions.append_audit(db, scope=scope, authorize=self.guards.audit_allowed,
            event=self._event(db, scope, binding.action, "ACTION_SUCCEEDED", approval=binding.approval,
                              receipt=receipt_ref, job=binding.job))
        return receipt_ref

    def _job(self, job, binding):
        if (job is None or job.status != "running" or job.worker_id != binding.worker_id
                or job.attempts != binding.job_attempt or job.locked_at is None
                or utc(job.locked_at) != binding.locked_at or job.lease_expires_at is None
                or utc(job.lease_expires_at) <= self.guards.now() or job.cancelled_at is not None
                or (job.result or {}).get("cancel_requested")
                or job.idempotency_key != binding.command_key):
            raise TrustConflict("stale_dispatch_binding")
