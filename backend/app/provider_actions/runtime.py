from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.jobs.queue import enqueue
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.v54_provider_action import (
    ProviderAction,
    ProviderActionApproval,
    ProviderDispatchOutbox,
    ProviderExecutionAttempt,
    ProviderOutcomeObservation,
)
from app.provider_actions.contracts import (
    ActionEnvelope,
    ExactApproval,
    LiveAuthorityResolver,
    ProviderActionAdapter,
    ProviderActionError,
    ProviderPreconditionFailed,
    ProviderReceipt,
    ProviderRequest,
    TimeoutAfterEffect,
    TimeoutBeforeEffect,
)


KIND = "v54.synthetic_provider_action"
PRODUCT_KIND = "provider.action.dispatch"
_installed_runtime = None


def install_synthetic_runtime(runtime):
    """Explicit test composition only; application startup never calls this."""
    global _installed_runtime
    _installed_runtime = runtime


def run_installed(payload):
    if _installed_runtime is None:
        raise ProviderActionError("synthetic_only")
    from app.jobs.queue import current_execution_claim
    owner = current_execution_claim()
    if owner is None:
        raise ProviderActionError("dispatch_binding_mismatch")
    return _installed_runtime.execute_job(payload, owner)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class ProviderActionRuntime:
    """Durable fail-closed adapter/outbox/reconciliation orchestration."""

    def __init__(self, *, sessions, adapter: ProviderActionAdapter,
                 authority: LiveAuthorityResolver, clock=lambda: datetime.now(timezone.utc),
                 allow_product: bool = False):
        bind = sessions.kw["bind"]
        database = bind.url.database or ""
        if (not allow_product and bind.url.get_backend_name() == "postgresql"
                and not database.startswith("puw_v54_test_")):
            raise ProviderActionError("synthetic_only")
        expected_adapter = "google_workspace" if allow_product else "synthetic"
        if bind.url.get_backend_name() not in {"sqlite", "postgresql"} or adapter.name != expected_adapter:
            raise ProviderActionError("synthetic_only")
        self.sessions, self.adapter, self.authority, self.clock = sessions, adapter, authority, clock
        self.kind = PRODUCT_KIND if allow_product else KIND

    def freeze(self, envelope: ActionEnvelope, *, actor_id: str, correlation_id: str):
        with self.sessions.begin() as db:
            return self.freeze_in_session(
                db, envelope, actor_id=actor_id, correlation_id=correlation_id,
                clock=self.clock,
            )

    @classmethod
    def freeze_in_session(cls, db, envelope: ActionEnvelope, *, actor_id: str,
                          correlation_id: str,
                          clock=lambda: datetime.now(timezone.utc)):
        """Freeze through the shared ledger inside a caller-owned transaction.

        Product proposal endpoints use this entry point so drafting a protected
        application record and sealing its action remain one atomic operation.
        It deliberately does not approve, enqueue, dispatch, or touch a provider.
        """
        if not isinstance(envelope, ActionEnvelope):
            raise ProviderActionError("invalid_envelope")
        existing = db.get(ProviderAction, (envelope.action_id, envelope.revision))
        if existing is not None:
            if cls._envelope(existing) != envelope:
                raise ProviderActionError("command_conflict")
            return envelope
        cls._require_relation(db, envelope)
        values = asdict(envelope)
        values["evidence_pins"] = list(envelope.evidence_pins)
        row = ProviderAction(
            **values,
            envelope_hash=envelope.envelope_hash, state="FROZEN", created_by=actor_id,
            created_at=clock(),
        )
        db.add(row)
        cls._audit(db, "action_frozen", envelope.action_id, envelope.revision,
                   actor_id, correlation_id, envelope_hash=envelope.envelope_hash)
        db.flush()
        return envelope

    def approve(self, action_id: str, revision: int, *, approval_id: str, actor_id: str,
                expires_at: datetime, correlation_id: str):
        with self.sessions.begin() as db:
            row = self._action(db, action_id, revision, lock=True)
            envelope = self._envelope(row)
            self.authority.resolve(envelope, operation="dispatch")
            if _utc(expires_at) <= _utc(self.clock()):
                raise ProviderActionError("approval_expired")
            existing = db.get(ProviderActionApproval, approval_id)
            if existing is not None:
                self._require_approval(row, existing)
                return self._approval_value(existing)
            approval = ProviderActionApproval(
                id=approval_id, action_id=row.action_id, revision=row.revision,
                organization_id=row.organization_id, project_id=row.project_id,
                mailbox_key=row.mailbox_key, command_key=row.command_key,
                idempotency_key=row.idempotency_key, payload_hash=row.payload_hash,
                envelope_hash=row.envelope_hash, authority_epoch=row.authority_epoch,
                capability_version=row.capability_version,
                credential_generation=row.credential_generation, state="GRANTED",
                approved_by=actor_id, granted_at=self.clock(), expires_at=expires_at,
            )
            db.add(approval)
            row.state = "READY"
            self._audit(db, "approval_granted", row.action_id, row.revision,
                        actor_id, correlation_id, approval_id=approval_id)
            db.flush()
            return self._approval_value(approval)

    def request_dispatch(self, action_id: str, revision: int, approval_id: str, *,
                         actor_id: str, correlation_id: str):
        with self.sessions.begin() as db:
            row = self._action(db, action_id, revision, lock=True)
            approval = db.get(ProviderActionApproval, approval_id)
            if approval is None:
                raise ProviderActionError("approval_required")
            self._require_approval(row, approval)
            self._require_live_approval(approval)
            existing = db.get(ProviderDispatchOutbox, (action_id, revision))
            if existing:
                if existing.approval_id != approval_id or existing.envelope_hash != row.envelope_hash:
                    raise ProviderActionError("dispatch_binding_mismatch")
                return {"action_id": action_id, "revision": revision, "approval_id": approval_id}
            outbox = ProviderDispatchOutbox(
                action_id=action_id, revision=revision, organization_id=row.organization_id,
                approval_id=approval_id, envelope_hash=row.envelope_hash, pending=True,
                created_at=self.clock(),
            )
            db.add(outbox)
            self._audit(db, "dispatch_requested", row.action_id, row.revision,
                        actor_id, correlation_id, approval_id=approval_id)
            db.flush()
            return {"action_id": action_id, "revision": revision, "approval_id": approval_id}

    def enqueue_action(self, action_id: str, revision: int) -> int:
        with self.sessions.begin() as db:
            row = self._action(db, action_id, revision)
            outbox = db.get(ProviderDispatchOutbox, (action_id, revision))
            approval = db.get(ProviderActionApproval, outbox.approval_id) if outbox else None
            if not outbox or not outbox.pending or not approval:
                raise ProviderActionError("approval_required")
            self._require_approval(row, approval)
            payload = {"organization_id": row.organization_id, "action_id": row.action_id, "revision": row.revision}
            idempotency_key = row.idempotency_key
            seal = row.envelope_hash
        with self.sessions() as queue_db:
            job = enqueue(queue_db, self.kind, payload, idempotency_key=idempotency_key, max_attempts=3)
            if job.kind != self.kind or job.payload != payload:
                raise ProviderActionError("dispatch_binding_mismatch")
            job_id = job.id
        with self.sessions.begin() as db:
            outbox = db.get(ProviderDispatchOutbox, (action_id, revision))
            if not outbox or outbox.envelope_hash != seal or outbox.job_id not in (None, job_id):
                raise ProviderActionError("dispatch_binding_mismatch")
            outbox.job_id = job_id
        return job_id

    def recover_outbox(self, limit: int = 100) -> int:
        with self.sessions() as db:
            pending = list(db.execute(select(ProviderDispatchOutbox.action_id, ProviderDispatchOutbox.revision)
                                      .where(ProviderDispatchOutbox.pending.is_(True),
                                             ProviderDispatchOutbox.job_id.is_(None))
                                      .order_by(ProviderDispatchOutbox.created_at).limit(limit)))
        recovered = 0
        for action_id, revision in pending:
            try:
                self.enqueue_action(action_id, revision)
            except ProviderActionError:
                continue
            recovered += 1
        return recovered

    def execute_job(self, payload: dict, owner: tuple):
        if set(payload) != {"organization_id", "action_id", "revision"}:
            raise ProviderActionError("dispatch_binding_mismatch")
        job_id, worker_id, job_attempt, locked_at = owner
        with self.sessions.begin() as db:
            row, approval, outbox = self._dispatch_binding(db, payload, owner)
            attempt = db.get(ProviderExecutionAttempt, (row.action_id, row.revision))
            if attempt is None:
                self.authority.resolve(self._envelope(row), operation="dispatch")
                self._require_live_approval(approval)
                attempt = ProviderExecutionAttempt(
                    action_id=row.action_id, revision=row.revision,
                    attempt_id=str(uuid4()), organization_id=row.organization_id,
                    first_job_id=job_id, adapter_name=self.adapter.name,
                    state="DISPATCHING", started_at=self.clock(),
                )
                db.add(attempt)
                row.state = "EXECUTING"
                self._audit(db, "dispatch_authorized", row.action_id, row.revision,
                            worker_id, f"job-{job_id}", job_id=job_id)
                request = self._request(row)
                source = "DISPATCH"
            elif attempt.state in {"APPLIED", "NOT_APPLIED"}:
                return self._result(db, row)
            else:
                request = self._request(row)
                source = "PROCESS_RECOVERY"
            action_id, revision = row.action_id, row.revision

        if source == "PROCESS_RECOVERY":
            return self._reconcile_request(action_id, revision, request,
                                           source=source, job_id=job_id)
        try:
            receipt = self.adapter.dispatch(request)
        except ProviderPreconditionFailed:
            return self._record(action_id, revision, "NOT_APPLIED", source="DISPATCH",
                                job_id=job_id, retry_safe=False, safe_code="precondition_failed")
        except TimeoutBeforeEffect:
            return self._record(action_id, revision, "NOT_APPLIED", source="DISPATCH",
                                job_id=job_id, retry_safe=True, safe_code="timeout_before_effect")
        except TimeoutAfterEffect:
            return self._record(action_id, revision, "UNKNOWN", source="DISPATCH",
                                job_id=job_id, retry_safe=False, safe_code="timeout_after_effect")
        except Exception:
            # An arbitrary adapter failure after dispatch entry is ambiguous. No
            # exception text is retained and the queue must not blind-retry it.
            return self._record(action_id, revision, "UNKNOWN", source="DISPATCH",
                                job_id=job_id, retry_safe=False, safe_code="adapter_failure")
        try:
            self._validate_receipt(request, receipt)
        except ProviderActionError:
            # A malformed response is not evidence of absence. Persist UNKNOWN
            # so the generic queue cannot retry the provider mutation blindly.
            return self._record(action_id, revision, "UNKNOWN", source="DISPATCH",
                                job_id=job_id, retry_safe=False, safe_code="provider_receipt_mismatch")
        return self._record(action_id, revision, receipt.outcome, source="DISPATCH",
                            job_id=job_id, retry_safe=receipt.retry_safe,
                            external_ref=receipt.external_ref)

    def reconcile(self, action_id: str, revision: int, *, actor_id: str, correlation_id: str):
        return self._reconcile_with_job(
            action_id, revision, actor_id=actor_id, correlation_id=correlation_id, job_id=None,
        )

    def _reconcile_with_job(
        self,
        action_id: str,
        revision: int,
        *,
        actor_id: str,
        correlation_id: str,
        job_id: int | None,
    ):
        with self.sessions.begin() as db:
            row = self._action(db, action_id, revision, lock=True)
            attempt = db.get(ProviderExecutionAttempt, (action_id, revision))
            if not attempt or attempt.state not in {"DISPATCHING", "UNKNOWN"}:
                raise ProviderActionError("outcome_not_reconcilable")
            self.authority.resolve(self._envelope(row), operation="reconcile")
            request = self._request(row)
            self._audit(db, "reconciliation_requested", action_id, revision,
                        actor_id, correlation_id)
        return self._reconcile_request(
            action_id, revision, request, source="RECONCILE", job_id=job_id,
        )

    def record_late_receipt(self, action_id: str, revision: int, receipt: ProviderReceipt, *,
                            actor_id: str, correlation_id: str):
        with self.sessions.begin() as db:
            row = self._action(db, action_id, revision, lock=True)
            attempt = db.get(ProviderExecutionAttempt, (action_id, revision))
            if not attempt or attempt.state not in {"DISPATCHING", "UNKNOWN"}:
                raise ProviderActionError("outcome_not_reconcilable")
            self.authority.resolve(self._envelope(row), operation="reconcile")
            request = self._request(row)
            self._validate_receipt(request, receipt)
            self._audit(db, "late_receipt_observed", action_id, revision, actor_id, correlation_id)
        return self._record(action_id, revision, receipt.outcome, source="LATE_RECEIPT", job_id=None,
                            retry_safe=receipt.retry_safe, external_ref=receipt.external_ref, late=True)

    def _reconcile_request(self, action_id, revision, request, *, source, job_id):
        with self.sessions() as db:
            row = self._action(db, action_id, revision)
            self.authority.resolve(self._envelope(row), operation="reconcile")
        try:
            receipt = self.adapter.lookup(request)
        except Exception:
            receipt = None
        if receipt is None:
            with self.sessions() as db:
                latest = self._latest(db, action_id, revision)
                if latest and latest.outcome == "UNKNOWN":
                    return self._result(db, self._action(db, action_id, revision))
            return self._record(action_id, revision, "UNKNOWN", source=source, job_id=job_id,
                                retry_safe=False, safe_code="receipt_not_found")
        self._validate_receipt(request, receipt)
        with self.sessions() as db:
            prior = self._latest(db, action_id, revision)
            late = bool(prior and prior.outcome == "UNKNOWN")
        return self._record(action_id, revision, receipt.outcome, source=source, job_id=job_id,
                            retry_safe=receipt.retry_safe, external_ref=receipt.external_ref, late=late)

    def _record(self, action_id, revision, outcome, *, source, job_id, retry_safe,
                external_ref=None, safe_code=None, late=False):
        if outcome == "UNKNOWN":
            retry_safe = False
        if outcome == "APPLIED":
            retry_safe = False
        with self.sessions.begin() as db:
            row = self._action(db, action_id, revision, lock=True)
            attempt = db.get(ProviderExecutionAttempt, (action_id, revision))
            if not attempt:
                raise ProviderActionError("dispatch_binding_mismatch")
            latest = self._latest(db, action_id, revision)
            if latest and latest.outcome == outcome and latest.source == source and latest.external_ref == external_ref:
                return self._result(db, row)
            sequence = (latest.sequence if latest else 0) + 1
            observation = ProviderOutcomeObservation(
                action_id=action_id, revision=revision, organization_id=row.organization_id,
                sequence=sequence, attempt_id=attempt.attempt_id, job_id=job_id,
                mailbox_key=row.mailbox_key, command_key=row.command_key,
                idempotency_key=row.idempotency_key, payload_hash=row.payload_hash,
                envelope_hash=row.envelope_hash, outcome=outcome, retry_safe=retry_safe,
                source=source, late=late, external_ref=external_ref, safe_code=safe_code,
                recorded_at=self.clock(),
            )
            db.add(observation)
            attempt.state = outcome
            attempt.completed_at = self.clock()
            row.state = outcome
            outbox = db.get(ProviderDispatchOutbox, (action_id, revision))
            if outbox:
                outbox.pending = False
            self._audit(db, "outcome_observed", action_id, revision, "provider-runtime",
                        f"observation-{sequence}", outcome=outcome, source=source,
                        retry_safe=retry_safe, late=late)
            db.flush()
            return {"action_id": action_id, "revision": revision, "outcome": outcome,
                    "retry_safe": retry_safe}

    def _dispatch_binding(self, db, payload, owner):
        job_id, worker_id, job_attempt, locked_at = owner
        row = self._action(db, payload["action_id"], payload["revision"], lock=True)
        outbox = db.get(ProviderDispatchOutbox, (row.action_id, row.revision))
        approval = db.get(ProviderActionApproval, outbox.approval_id) if outbox else None
        job = db.get(BackgroundJob, job_id)
        expected_payload = {"organization_id": row.organization_id, "action_id": row.action_id, "revision": row.revision}
        if (type(payload["organization_id"]) is not int or payload != expected_payload
                or not outbox or not approval or outbox.job_id not in (None, job_id)
                or outbox.envelope_hash != row.envelope_hash or not job
                or job.kind != self.kind or job.payload != expected_payload or job.idempotency_key != row.idempotency_key
                or job.status != "running" or job.worker_id != worker_id or job.attempts != job_attempt
                or not job.locked_at or _utc(job.locked_at) != _utc(locked_at) or not job.lease_expires_at
                or _utc(job.lease_expires_at) <= _utc(self.clock())):
            raise ProviderActionError("dispatch_binding_mismatch")
        try:
            self._require_approval(row, approval)
        except ProviderActionError:
            raise ProviderActionError("dispatch_binding_mismatch") from None
        if outbox.job_id is None:
            outbox.job_id = job_id
        return row, approval, outbox

    @staticmethod
    def _require_relation(db, envelope):
        if not envelope.relation_kind:
            return
        originals = list(db.scalars(select(ProviderAction).where(
            ProviderAction.action_id == envelope.relation_action_id,
            ProviderAction.organization_id == envelope.organization_id)))
        # a06 has no relation_action_revision column. Refuse an ambiguous
        # relation instead of silently selecting one of several revisions.
        original = originals[0] if len(originals) == 1 else None
        latest = ProviderActionRuntime._latest(
            db, original.action_id, original.revision,
        ) if original else None
        required = {
            "ROLLBACK": ("REVERSIBLE", "synthetic.effect.rollback"),
            "COMPENSATION": ("COMPENSATABLE", "synthetic.effect.compensate"),
            "CORRECTIVE": ("IRREVERSIBLE", "synthetic.effect.corrective"),
        }[envelope.relation_kind]
        if (not original or not latest or latest.outcome != "APPLIED"
                or original.state != "APPLIED"
                or original.project_id != envelope.project_id or original.mailbox_key != envelope.mailbox_key
                or original.evidence_pins != list(envelope.evidence_pins)
                or original.reversibility != required[0] or envelope.action_kind != required[1]
                or (envelope.relation_kind == "CORRECTIVE" and original.action_kind != "synthetic.effect.send")):
            raise ProviderActionError("relation_invalid")

    @staticmethod
    def _action(db, action_id, revision, lock=False):
        query = select(ProviderAction).where(ProviderAction.action_id == action_id,
                                             ProviderAction.revision == revision)
        if lock:
            query = query.with_for_update()
        row = db.scalar(query)
        if row is None:
            raise ProviderActionError("dispatch_binding_mismatch")
        return row

    @staticmethod
    def _envelope(row):
        return ActionEnvelope(
            action_id=row.action_id, revision=row.revision, organization_id=row.organization_id,
            project_id=row.project_id, mailbox_key=row.mailbox_key, provider=row.provider,
            mode=row.mode, synthetic_only=row.synthetic_only, action_kind=row.action_kind,
            reversibility=row.reversibility, payload_hash=row.payload_hash,
            command_key=row.command_key, idempotency_key=row.idempotency_key,
            context_revision=row.context_revision, evidence_pins=tuple(row.evidence_pins),
            authority_epoch=row.authority_epoch, capability_version=row.capability_version,
            credential_generation=row.credential_generation, relation_kind=row.relation_kind,
            relation_action_id=row.relation_action_id,
        )

    @staticmethod
    def _request(row):
        return ProviderRequest(
            action_id=row.action_id, revision=row.revision, organization_id=row.organization_id,
            project_id=row.project_id, mailbox_key=row.mailbox_key, command_key=row.command_key,
            idempotency_key=row.idempotency_key, payload_hash=row.payload_hash,
            action_kind=row.action_kind, capability_version=row.capability_version,
            credential_generation=row.credential_generation,
        )

    @staticmethod
    def _approval_value(row):
        return ExactApproval(
            id=row.id, action_id=row.action_id, revision=row.revision,
            organization_id=row.organization_id, project_id=row.project_id,
            mailbox_key=row.mailbox_key, command_key=row.command_key,
            idempotency_key=row.idempotency_key, payload_hash=row.payload_hash,
            envelope_hash=row.envelope_hash, authority_epoch=row.authority_epoch,
            capability_version=row.capability_version,
            credential_generation=row.credential_generation,
            expires_at=_utc(row.expires_at),
        )

    @staticmethod
    def _require_approval(row, approval):
        expected = (row.action_id, row.revision, row.organization_id, row.project_id, row.mailbox_key,
                    row.command_key, row.idempotency_key, row.payload_hash, row.envelope_hash,
                    row.authority_epoch, row.capability_version, row.credential_generation)
        actual = (approval.action_id, approval.revision, approval.organization_id, approval.project_id,
                  approval.mailbox_key, approval.command_key, approval.idempotency_key,
                  approval.payload_hash, approval.envelope_hash, approval.authority_epoch,
                  approval.capability_version, approval.credential_generation)
        if actual != expected or approval.state != "GRANTED":
            raise ProviderActionError("approval_mismatch")

    def _require_live_approval(self, approval):
        if approval.state != "GRANTED":
            raise ProviderActionError("approval_mismatch")
        if _utc(approval.expires_at) <= _utc(self.clock()):
            raise ProviderActionError("approval_expired")

    @staticmethod
    def _validate_receipt(request, receipt):
        expected = (request.action_id, request.revision, request.organization_id, request.project_id,
                    request.mailbox_key, request.command_key, request.idempotency_key, request.payload_hash)
        actual = (receipt.action_id, receipt.revision, receipt.organization_id, receipt.project_id,
                  receipt.mailbox_key, receipt.command_key, receipt.idempotency_key, receipt.payload_hash)
        if actual != expected or receipt.outcome not in {"APPLIED", "NOT_APPLIED", "UNKNOWN"}:
            raise ProviderActionError("provider_receipt_mismatch")
        if receipt.outcome == "UNKNOWN" and receipt.retry_safe:
            raise ProviderActionError("provider_receipt_mismatch")

    @staticmethod
    def _latest(db, action_id, revision):
        return db.scalar(select(ProviderOutcomeObservation).where(
            ProviderOutcomeObservation.action_id == action_id,
            ProviderOutcomeObservation.revision == revision,
        ).order_by(ProviderOutcomeObservation.sequence.desc()).limit(1))

    def _result(self, db, row):
        latest = self._latest(db, row.action_id, row.revision)
        if latest is None:
            return {"action_id": row.action_id, "revision": row.revision,
                    "outcome": "UNKNOWN", "retry_safe": False}
        return {"action_id": row.action_id, "revision": row.revision,
                "outcome": latest.outcome, "retry_safe": latest.retry_safe}

    @staticmethod
    def _audit(db, event, action_id, revision, actor_id, correlation_id, **safe):
        # Actor/correlation inputs may originate outside this seam. Preserve
        # linkage without retaining accidental labels, addresses or PII.
        actor_hash = sha256(str(actor_id).encode()).hexdigest()
        correlation_hash = sha256(str(correlation_id).encode()).hexdigest()
        details = {"action_id": action_id, "revision": revision, "actor_hash": actor_hash,
                   "correlation_hash": correlation_hash, **safe}
        db.add(AuditLog(action=f"v54.provider.{event}", entity_type="provider_action",
                        entity_id=None, details=json.dumps(details, sort_keys=True, separators=(",", ":"))))
