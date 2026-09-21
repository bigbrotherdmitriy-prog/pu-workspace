"""DB-only writer for the closed ``notification.internal.create`` AUTO action."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.action_trust.guards import TrustConflict, reference
from app.action_trust.validation import live_pins
from app.core.v54_dto import ActionEnvelope, canonical_hash
from app.models.job import BackgroundJob
from app.models.management import ManagementHistory, Notification
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_pilot import ActionReceipt, ActionRevision, PendingDispatch, PilotAction


_KIND = "auto_task_due_soon"


class InternalNotificationMutation:
    """Join Trust T2; never commit, enqueue, or call an external provider."""

    def __init__(self, *, guards, trust=None, project_hourly_quota=3,
                 recipient_daily_quota=10):
        self.guards, self.trust = guards, trust
        if (type(project_hourly_quota) is not int or not 1 <= project_hourly_quota <= 3
                or type(recipient_daily_quota) is not int
                or not 1 <= recipient_daily_quota <= 10):
            raise ValueError("notification_quota_invalid")
        self.project_hourly_quota = project_hourly_quota
        self.recipient_daily_quota = recipient_daily_quota

    def apply(self, db, *, scope, binding):
        revision = db.get(ActionRevision, (binding.action.ref.id.value, binding.action.value))
        action = db.get(PilotAction, binding.action.ref.id.value)
        if revision is None or action is None:
            raise TrustConflict("notification_binding_mismatch")
        envelope = ActionEnvelope.model_validate(revision.envelope)
        self.guards.enabled(db, scope, envelope.action_type)
        if (envelope.action_type != "notification.internal.create"
                or action.business_state != "EXECUTING" or action.reservation_fence <= 0
                or envelope.requested_by != scope.actor
                or revision.command_key != binding.command_key
                or revision.envelope_hash != binding.envelope_hash):
            raise TrustConflict("notification_binding_mismatch")
        pending = db.get(PendingDispatch, action.id, populate_existing=True)
        if (pending is None or not pending.pending or pending.organization_id != action.organization_id
                or (pending.revision, pending.envelope_hash, pending.authorization_origin,
                    pending.approval_id, pending.job_id) != (
                    revision.revision, revision.envelope_hash, binding.authorization_origin,
                    binding.approval.id.value if binding.approval else None, int(binding.job.id.value))
                or any(getattr(binding, key) != value
                       for key, value in self._authorization(pending, scope).items())
                or db.scalar(select(ActionReceipt.id).where(ActionReceipt.action_id == action.id))):
            raise TrustConflict("notification_binding_mismatch")
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == int(binding.job.id.value))
                        .with_for_update().execution_options(populate_existing=True))
        self._job(job, binding)
        live_pins(db, guards=self.guards, scope=scope, envelope=envelope,
                  action=action, operation="dispatch")

        # Serialize quota evaluation per project.  This is the same project lock
        # already acquired by Trust, repeated here only as a domain assertion.
        project = db.scalar(select(Project).where(Project.id == action.project_id).with_for_update())
        task = db.scalar(select(Task).where(Task.id == int(envelope.target.ref.id.value))
                         .with_for_update().execution_options(populate_existing=True))
        payload = envelope.payload
        recipient_id = int(payload.recipient_ref.id.value)
        member = db.scalar(select(ProjectMember.id).where(
            ProjectMember.project_id == action.project_id,
            ProjectMember.user_id == recipient_id,
        ))
        if (project is None or task is None or task.project_id != action.project_id
                or task.record_version != envelope.target.value or task.due_date is None
                or task.due_date.isoformat() != payload.due_date or member is None
                or recipient_id not in {int(scope.actor.id.value), task.assignee_user_id}):
            raise TrustConflict("notification_target_changed")

        digest = canonical_hash({
            "template": payload.template_id,
            "recipient": recipient_id,
            "task": task.id,
            "condition": payload.condition_key,
            "due_date": payload.due_date,
        })
        dedupe_key = f"auto-notification:{digest}"
        existing = db.scalar(select(Notification).where(
            Notification.user_id == recipient_id,
            Notification.dedupe_key == dedupe_key,
        ))
        if existing is not None:
            if (existing.project_id != action.project_id or existing.kind != _KIND
                    or existing.entity_type != "task" or existing.entity_id != task.id):
                raise TrustConflict("notification_dedupe_conflict")
            return reference(scope, "notification", existing.id)

        now = self.guards.now()
        project_used = db.scalar(select(func.count()).select_from(Notification).where(
            Notification.project_id == action.project_id,
            Notification.kind == _KIND,
            Notification.created_at >= now - timedelta(hours=1),
        )) or 0
        recipient_used = db.scalar(select(func.count()).select_from(Notification).where(
            Notification.user_id == recipient_id,
            Notification.kind == _KIND,
            Notification.created_at >= now - timedelta(days=1),
        )) or 0
        if (project_used >= self.project_hourly_quota
                or recipient_used >= self.recipient_daily_quota):
            raise TrustConflict("notification_quota_exhausted")

        candidate = Notification(
            project_id=action.project_id, user_id=recipient_id, kind=_KIND,
            title="Задача требует внимания",
            body=f"Срок задачи: {payload.due_date}. Откройте задачу в проекте.",
            entity_type="task", entity_id=task.id, dedupe_key=dedupe_key,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
        except IntegrityError:
            candidate = db.scalar(select(Notification).where(
                Notification.user_id == recipient_id,
                Notification.dedupe_key == dedupe_key,
            ))
            if candidate is None:
                raise
            return reference(scope, "notification", candidate.id)
        db.add(ManagementHistory(
            organization_id=action.organization_id, project_id=action.project_id,
            entity_type="notification", entity_id=candidate.id, record_version=1,
            action="auto_created", actor_user_id=int(scope.actor.id.value),
            idempotency_key=f"auto-notif:{digest[:64]}", command_hash=revision.envelope_hash,
            old_values={}, new_values={"kind": _KIND, "is_read": False},
            evidence={"task_id": task.id, "task_record_version": task.record_version,
                      "template_id": payload.template_id},
            reason="Фиксированный внутренний шаблон; внешняя доставка отключена",
        ))
        db.flush()
        return reference(scope, "notification", candidate.id)

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


class InternalMutationRouter:
    """Closed two-entry mutation registry; unknown actions fail closed."""

    def __init__(self, *, task, notification):
        self.task, self.notification = task, notification

    def apply(self, db, *, scope, binding):
        row = db.get(ActionRevision, (binding.action.ref.id.value, binding.action.value))
        if row is None:
            raise TrustConflict("resource_unavailable")
        action_type = ActionEnvelope.model_validate(row.envelope).action_type
        if action_type in {"task.internal.create", "task.internal.cancel"}:
            return self.task.apply(db, scope=scope, binding=binding)
        if action_type == "notification.internal.create":
            return self.notification.apply(db, scope=scope, binding=binding)
        raise TrustConflict("resource_unavailable")
