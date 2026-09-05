"""Internal Task domain writer, joining Trust's T2. Never call a legacy endpoint.

Not an independent executor: a live sealed DispatchBinding and EXECUTING
reservation are mandatory. No provider, notification, Obligation or commit.
"""
from datetime import date

from sqlalchemy import select

from app.action_trust.guards import TrustConflict, reference
from app.action_trust.validation import live_pins
from app.core.v54_dto import canonical_hash
from app.models.job import BackgroundJob
from app.models.management import Obligation
from app.models.task import Task, TaskHistory
from app.models.v54_pilot import ActionReceipt, ActionRevision, PendingDispatch, PilotAction


class InternalTaskMutation:
    def __init__(self, *, guards, trust=None):
        self.guards = guards
        self.trust = trust

    def apply(self, db, *, scope, binding):
        # TrustFacade owns authorization and the Project -> Authority -> policy
        # -> action locks.  This helper is deliberately not an independent
        # executor; it consumes the already-locked T2 reservation only.
        revision = db.get(ActionRevision, (binding.action.ref.id.value, binding.action.value))
        action = db.get(PilotAction, binding.action.ref.id.value)
        if revision is None or action is None:
            raise TrustConflict("task_binding_mismatch")
        from app.core.v54_dto import ActionEnvelope
        envelope = ActionEnvelope.model_validate(revision.envelope)
        self.guards.enabled(db, scope, envelope.action_type)
        if (action.business_state != "EXECUTING" or action.reservation_fence <= 0
                or envelope.requested_by != scope.actor
                or revision.command_key != binding.command_key
                or revision.envelope_hash != binding.envelope_hash):
            raise TrustConflict("task_binding_mismatch")
        pending = db.get(PendingDispatch, action.id, populate_existing=True)
        if (pending is None or not pending.pending or pending.organization_id != action.organization_id
                or (pending.revision, pending.envelope_hash, pending.authorization_origin,
                    pending.approval_id, pending.job_id) != (
                    revision.revision, revision.envelope_hash, binding.authorization_origin,
                    binding.approval.id.value if binding.approval else None, int(binding.job.id.value))
                or any(getattr(binding, key) != value
                       for key, value in self._authorization(pending, scope).items())
                or db.scalar(select(ActionReceipt.id).where(ActionReceipt.action_id == action.id))):
            raise TrustConflict("task_binding_mismatch")
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == int(binding.job.id.value))
                        .with_for_update().execution_options(populate_existing=True))
        self._job(job, binding)
        live_pins(db, guards=self.guards, scope=scope, envelope=envelope, action=action, operation="dispatch")
        if envelope.action_type == "task.internal.create":
            payload = envelope.payload
            # Assignee membership is an explicit domain permission, not just existence.
            self.guards.allow(db, scope, "task.assign", payload.assignee_ref)
            task = Task(project_id=action.project_id, message_id=action.message_id,
                assignee_user_id=int(payload.assignee_ref.id.value), created_by_user_id=int(scope.actor.id.value),
                title=payload.title, due_date=date.fromisoformat(payload.due_date), status="assigned",
                record_version=1, source_type="v54_synthetic", source_file_id=action.id,
                source_file_name="V5.4 internal action", source_excerpt="",
                source_excerpt_hash=canonical_hash({"action_id": action.id}), confidence=1.0,
                needs_review=False, external_action_status="not_requested")
            db.add(task)
            old_status = None
        else:
            task = db.scalar(select(Task).where(Task.id == int(envelope.target.ref.id.value))
                .with_for_update().execution_options(populate_existing=True))
            if (task is None or task.source_type != "v54_synthetic" or task.message_id != action.message_id
                    or task.project_id != action.project_id or task.record_version != envelope.target.value
                    or task.status != "assigned" or task.external_action_status != "not_requested"
                    or task.google_task_id or task.google_calendar_event_id
                    or db.scalar(select(Obligation.id).where(Obligation.task_id == task.id).limit(1))):
                raise TrustConflict("task_cancel_dependency")
            old_status = task.status
            task.status, task.record_version = "cancelled", task.record_version + 1
        db.flush()
        db.add(TaskHistory(task_id=task.id, action="created" if old_status is None else "cancelled",
            old_status=old_status, new_status=task.status, changed_by_user_id=int(scope.actor.id.value)))
        db.flush()
        return reference(scope, "task", task.id)

    def _authorization(self, pending, scope):
        if self.trust is not None:
            return self.trust._pending_authorization(pending, scope)
        from app.action_trust.facade import TrustFacade
        return TrustFacade._pending_authorization(pending, scope)

    def _job(self, job, binding):
        if self.trust is not None:
            return self.trust._job(job, binding)
        from app.action_trust.facade import TrustFacade
        return TrustFacade(guards=self.guards)._job(job, binding)
