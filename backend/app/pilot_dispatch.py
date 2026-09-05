"""T1 recovery / T2 bridge using the existing queue. Disabled unless injected.

Only an isolated synthetic harness may install a runtime; no environment flag
alone supplies authority. Production loader/cutover is deliberately unavailable.
"""
from uuid import UUID
from types import SimpleNamespace

from sqlalchemy import select

from app.action_trust.guards import TrustConflict, reference, revision, utc
from app.core.v54_interfaces import DispatchBinding, RequestScope
from app.core.v54_refs import ObjectRef, TaggedId
from app.jobs.queue import enqueue
from app.models.job import BackgroundJob
from app.models.v54_pilot import ActionRevision, PendingDispatch, PilotAction

KIND = "v54.synthetic_task"
_runtime = None


def synthetic_command_key(action, number):
    """Server factory: global queue namespace, no text/hash of source material."""
    if action.type != "action" or type(number) is not int or number <= 0:
        raise TrustConflict("invalid_action_pin")
    return f"v54:{action.tenant_id.value}:{action.id.value}:{number}"


def install_synthetic_runtime(runtime):
    """Harness-only composition root. Not called by product main/startup."""
    global _runtime
    _runtime = runtime


def recover_installed():
    return _runtime.recover() if _runtime is not None else 0


def run_installed(payload):
    if _runtime is None:
        raise TrustConflict("pilot_authority_not_configured")
    from app.jobs.queue import current_execution_claim
    owner = current_execution_claim()
    if owner is None:
        raise TrustConflict("pilot_worker_binding_required")
    return _runtime.execute(payload, owner)


class SyntheticDispatch:
    def __init__(self, *, sessions, composition_for_scope):
        # Defense in depth: never install this snapshot-authority harness against
        # the ordinary product database. This does not authorize real data there.
        url = sessions.kw["bind"].url
        if url.get_backend_name() == "postgresql":
            if not (url.database or "").startswith("puw_v54_test_"):
                raise TrustConflict("synthetic_database_required")
        elif url.get_backend_name() != "sqlite":
            raise TrustConflict("synthetic_database_required")
        self.sessions, self.composition_for_scope = sessions, composition_for_scope

    @staticmethod
    def _scope(action, sealed, correlation):
        if str(UUID(correlation)) != correlation:
            raise TrustConflict("invalid_correlation_id")
        tenant = TaggedId(kind="int", value=str(action.organization_id))
        return RequestScope(tenant=tenant, actor=ObjectRef(namespace="pu", type="user", tenant_id=tenant,
            id=TaggedId(kind="int", value=str(sealed.requested_by))),
            project=ObjectRef(namespace="pu", type="project", tenant_id=tenant,
                id=TaggedId(kind="int", value=str(action.project_id))), correlation_id=correlation)

    def enqueue_action(self, action_id, correlation):
        # Read and recheck T1 in its own transaction. enqueue commits separately.
        with self.sessions.begin() as db:
            action = db.get(PilotAction, action_id)
            pending = db.get(PendingDispatch, action_id)
            if not action or not pending or not pending.pending:
                return None
            sealed = db.get(ActionRevision, (action.id, pending.revision))
            scope = self._scope(action, sealed, correlation)
            component = self.composition_for_scope(scope)
            component.guards.enabled(db, scope, action.action_type)
            pin = revision(reference(scope, "action", action.id), pending.revision)
            action, sealed, envelope = component.trust._load(db, scope, pin, operation="action.dispatch")
            authorization = component.trust._pending_authorization(pending, scope)
            if pending.authorization_origin == "HUMAN_APPROVAL":
                component.trust._grant(db, scope, action, sealed, pin, authorization["approval"])
            else:
                component.trust._server_policy(
                    db, scope, sealed, envelope,
                    SimpleNamespace(envelope_hash=sealed.envelope_hash, **authorization),
                )
            payload = {"tenant_id": action.organization_id, "action_id": action.id,
                       "revision": sealed.revision, "correlation_id": correlation}
            key = sealed.command_key
            seal, approval_id = sealed.envelope_hash, pending.approval_id
            authorization_snapshot = {
                key: getattr(pending, key) for key in (
                    "authorization_origin", "policy_id", "policy_revision", "policy_hash",
                    "authority_epoch", "decision_hash", "action_hash", "payload_hash",
                    "authorization_decision", "authorization_valid_until",
                )
            }
        with self.sessions() as queue_db:
            job = enqueue(queue_db, KIND, payload, idempotency_key=key)
            # Global queue keys must never bind to another tenant/kind/action.
            actual = job.payload or {}
            if (job.kind != KIND or any(actual.get(k) != payload[k] for k in ("tenant_id", "action_id", "revision"))):
                raise TrustConflict("pilot_queue_key_conflict")
            job_id = job.id
        # Crash here leaves a queued job; repeat enqueue returns its stable key.
        with self.sessions.begin() as db:
            component.guards.allow(db, scope, "action.dispatch", pin.ref)
            action = db.scalar(select(PilotAction).where(PilotAction.id == action_id).with_for_update())
            pending = db.get(PendingDispatch, action_id, populate_existing=True)
            if (not pending or not pending.pending or (pending.revision, pending.envelope_hash, pending.approval_id)
                    != (pin.value, seal, approval_id) or action.current_revision != pin.value):
                raise TrustConflict("pilot_intent_changed")
            if any(getattr(pending, key) != value for key, value in authorization_snapshot.items()):
                raise TrustConflict("pilot_intent_changed")
            pending.job_id = job_id
        return job_id

    def recover(self, limit=100):
        from uuid import uuid4
        with self.sessions() as db:
            ids = list(db.scalars(select(PendingDispatch.action_id).where(PendingDispatch.pending.is_(True),
                PendingDispatch.job_id.is_(None)).order_by(PendingDispatch.action_id).limit(limit)))
        count = 0
        for ident in ids:
            try:
                count += self.enqueue_action(ident, str(uuid4())) is not None
            except ValueError:
                # Fail closed for expired/revoked/unconfigured intents; keep history.
                continue
        return count

    def execute(self, payload, owner):
        if set(payload) != {"tenant_id", "action_id", "revision", "correlation_id"}:
            raise TrustConflict("invalid_pilot_payload")
        job_id, worker_id, attempt, locked_at = owner
        with self.sessions.begin() as db:
            action = db.get(PilotAction, payload["action_id"])
            sealed = db.get(ActionRevision, (payload["action_id"], payload["revision"]))
            if not action or not sealed or action.organization_id != payload["tenant_id"]:
                raise TrustConflict("resource_unavailable")
            scope = self._scope(action, sealed, payload["correlation_id"])
            component = self.composition_for_scope(scope)
            component.guards.allow(db, scope, "action.receipt.read", reference(scope, "action", action.id))
            # Do not lock the action here: Trust T2 must first acquire the live
            # Project -> AuthorityState -> policy locks, then the action lock.
            # A worker may still arrive before the enqueue marker transaction.
            pending = db.get(PendingDispatch, action.id, populate_existing=True)
            job = db.get(BackgroundJob, job_id, populate_existing=True)
            if (not pending or not job or job.kind != KIND or job.payload != payload
                    or pending.job_id not in (None, job_id) or pending.revision != sealed.revision
                    or pending.envelope_hash != sealed.envelope_hash or job.idempotency_key != sealed.command_key):
                raise TrustConflict("pilot_dispatch_not_linked")
            if pending.job_id is None:
                if not pending.pending:
                    raise TrustConflict("pilot_dispatch_not_linked")
                pending.job_id = job_id
                db.flush()
            authorization = component.trust._pending_authorization(pending, scope)
            binding = DispatchBinding(action=revision(reference(scope, "action", action.id), sealed.revision),
                **authorization, envelope_hash=sealed.envelope_hash,
                command_key=sealed.command_key, job=reference(scope, "background_job", job_id),
                worker_id=worker_id, job_attempt=attempt, locked_at=utc(locked_at))
            receipt = component.trust.execute(db, scope=scope, binding=binding, mutation=component.mutation)
            action_type = action.action_type
        # Separate consumer transaction. Failure retries receipt, never Task mutation.
        if action_type == "task.internal.create" and component.enabled:
            with self.sessions.begin() as db:
                component.context(db, scope).project_receipt(db, scope=scope, receipt=receipt)
        return {"receipt_id": receipt.id.value}
