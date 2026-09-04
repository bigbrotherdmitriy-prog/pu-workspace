"""Explicit TEST DOUBLES. Not product Task execution, ACL, or worker wiring."""
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

import app.models
from app.action_trust.facade import TrustFacade
from app.action_trust.guards import Guards, reference
from app.core.v54_dto import ActionEnvelope, DeadlineClaimInput, canonical_hash
from app.core.v54_interfaces import DispatchBinding, PilotGate, Resolution, ReviewCommand
from app.core.v54_refs import ObjectRef, VersionPin
from app.database import Base
from app.models.job import BackgroundJob
from app.models.task import Task, TaskHistory
from app.models.v54_pilot import (
    ActionRevision, ContextRelation, DeadlineClaim, EvidenceAssessment, PendingDispatch, PilotAction,
)
from app.task_claims import DeadlineClaims
from v54_pilot_fixture import NOW, DOC_FIXTURE, envelopes, pin, ref, scope, seed, uid


class SyntheticAccess:
    """Fake roles/resolver, not proof of source policies or production authority locks."""
    def __init__(self):
        self.time = NOW
        self.denied = set()
        self.bad_pins = {}
        self.epochs = {2: 1, 3: 1}
        self.calls = []
        self.enabled = True

    def authorize(self, db, s, operation, subject, *, lock):
        self.calls.append((operation, subject.type, lock))
        actor = int(s.actor.id.value)
        if (operation in self.denied or (actor, operation) in self.denied
                or s.tenant.value != "1" or s.project.id.value != "4"):
            return False
        if operation in {"claim.review", "action.approve", "action.revoke"}:
            return actor == 3
        return actor in {2, 3}

    def gate(self, db, s):
        return PilotGate(synthetic_scope_authorized=self.enabled, roles_known=True,
                         retention_known=True, valid_until=NOW + timedelta(minutes=5))

    def resolve(self, db, *, scope, pin, operation, lock):
        self.calls.append((operation, pin.ref.type, lock))
        values = dict(acl="allow", version="current", freshness="fresh", availability="available",
            verification="verified", policy_known=True, retention_known=True, residency_allowed=True,
            valid_until=NOW + timedelta(minutes=5), authority_epoch=self.epochs[int(scope.actor.id.value)], binding_epoch=1)
        if pin.ref.type == "evidence":
            row = db.get(EvidenceAssessment, pin.ref.id.value, populate_existing=True)
            if row:
                values.update(verification=row.verification, freshness=row.freshness,
                              availability=row.availability, valid_until=row.valid_until)
        if pin.ref.type == "deadline_claim":
            row = db.get(DeadlineClaim, (pin.ref.id.value, pin.value), populate_existing=True)
            values["verification"] = "verified" if row and row.verification == "confirmed" else "unverified"
        # This fixture fakes existence/ACL of other refs; never claim runtime PASS.
        values.update(self.bad_pins.get(pin.ref.type, {}))
        from app.action_trust.guards import utc
        if values["valid_until"] is not None:
            values["valid_until"] = utc(values["valid_until"])
        return Resolution(pin=pin, actor=scope.actor, project=scope.project, operation=operation, **values)


class SyntheticTaskMutation:
    """DB-only TaskMutation TEST DOUBLE, not the legacy/product Task helper."""
    def __init__(self):
        self.calls = 0
        self.fail_after_flush = False
        self.after_mutation = None

    def apply(self, db, *, scope, binding):
        self.calls += 1
        assert db.in_transaction()
        row = db.get(ActionRevision, (binding.action.ref.id.value, binding.action.value))
        e = ActionEnvelope.model_validate(row.envelope)
        if e.action_type == "task.internal.create":
            task = Task(project_id=int(scope.project.id.value), assignee_user_id=int(e.payload.assignee_ref.id.value),
                created_by_user_id=int(scope.actor.id.value), title=e.payload.title, status="assigned",
                due_date=date.fromisoformat(e.payload.due_date), record_version=1,
                source_file_id="synthetic", source_file_name="synthetic", source_excerpt="",
                source_excerpt_hash=canonical_hash({"action": row.action_id}), confidence=1.0,
                needs_review=False, external_action_status="proposed")
            db.add(task)
        else:
            task = db.get(Task, int(e.target.ref.id.value))
            assert task.status == "assigned" and task.record_version == e.target.value
            task.status, task.record_version = "cancelled", task.record_version + 1
        db.flush()
        db.add(TaskHistory(task_id=task.id, action="created" if e.action_type.endswith("create") else "cancelled",
            old_status=None if e.action_type.endswith("create") else "assigned", new_status=task.status,
            changed_by_user_id=int(scope.actor.id.value)))
        db.flush()
        if self.after_mutation:
            self.after_mutation()
        if self.fail_after_flush:
            raise RuntimeError("synthetic_mutation_failure")
        return reference(scope, "task", task.id)


class Harness:
    def __init__(self, db):
        self.db = db
        self.access = SyntheticAccess()
        self.guards = Guards(resolver=self.access, authorize=self.access.authorize,
                             gate=self.access.gate, clock=lambda: self.access.time)
        self.trust = TrustFacade(guards=self.guards)
        self.claims = DeadlineClaims(guards=self.guards)
        self.requester = scope()
        self.reviewer = self.requester.model_copy(update={"actor": ObjectRef.model_validate(ref("user", 3))})
        self.mutation = SyntheticTaskMutation()

    def claim(self, anchor=101, revision_number=1, due_date="2026-09-10", confirm=True,
              due_time=None, timezone="Europe/Moscow", evidence_id=16):
        value = DeadlineClaimInput(anchor=ref("deadline_claim", uid(anchor)), revision=revision_number,
            message=ref("message", 6), due_date=due_date, due_time=due_time,
            timezone=timezone, evidence=[pin("evidence", uid(evidence_id))])
        result = self.claims.extract(self.db, scope=self.requester, claim=value)
        if confirm:
            self.claims.review(self.db, scope=self.reviewer,
                command=ReviewCommand(subject=result, expected_record_version=1, decision="confirmed"))
        return result

    def envelope(self, cancel_target=None):
        e = envelopes()[0 if cancel_target is None else 1]
        e["action_ref"] = ref("action", uid(120 if cancel_target is None else 121))
        e["claim"] = pin("deadline_claim", uid(101))
        e["idempotency_key"] = "synthetic-trust-create-1" if cancel_target is None else "synthetic-trust-cancel-1"
        if cancel_target is not None:
            e["target"] = pin("task", cancel_target, version_kind="record_version")
            e["compensates_action_ref"] = ref("action", uid(120))
        return ActionEnvelope.model_validate(e)

    def approve(self, e, key="approve-create"):
        return self.trust.approve(self.db, scope=self.reviewer,
            action=VersionPin(ref=e.action_ref, version_kind="revision", value=e.revision),
            envelope_hash=canonical_hash(e.model_dump(mode="json")), command_key=key,
            expires_at=NOW + timedelta(minutes=4))

    def prepare(self, e=None, approve_key="approve-create"):
        e = e or self.envelope()
        p = self.trust.freeze(self.db, scope=self.requester, envelope=e)
        a = self.approve(e, approve_key)
        obj = self.db.get(PilotAction, p.ref.id.value)
        self.trust.request_dispatch(self.db, scope=self.requester, action=p, approval=a,
                                    expected_record_version=obj.record_version)
        return e, p, a

    def attach_job(self, e, p, a):
        # Explicit test-only imitation of NEXT-STAGE recovery/wiring, no enqueue.
        job = BackgroundJob(kind="synthetic_trust", status="running", payload={},
            worker_id="synthetic-worker", attempts=1, locked_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=3), idempotency_key=e.idempotency_key)
        self.db.add(job)
        self.db.flush()
        self.db.get(PendingDispatch, p.ref.id.value).job_id = job.id
        self.db.flush()
        return DispatchBinding(action=p, approval=a, envelope_hash=canonical_hash(e.model_dump(mode="json")),
            command_key=e.idempotency_key, job=ref("background_job", job.id),
            worker_id=job.worker_id, job_attempt=job.attempts, locked_at=NOW)


@pytest.fixture
def h():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        with db.begin():
            seed(db)  # Unmodified foundation fixture; its action remains untouched.
            fixture = json.loads(DOC_FIXTURE.read_text(encoding="utf-8"))
            for r in fixture["records"]:
                if r["ref"]["type"] != "context_relation":
                    continue
                db.add(ContextRelation(id=r["ref"]["id"]["value"], organization_id=1,
                    lineage_id=r["lineage_id"], revision=r["revision"], message_id=6,
                    relation_type=r["relation_type"], target_ref=r["target_ref"], scope_ref=ref("project", 4),
                    expected_target={"ref": r["target_ref"], "version_kind": "record_version", "value": 1},
                    expected_context_version=1, evidence_pins=r["evidence"], provenance={"kind": "synthetic"},
                    state="confirmed", applicability="current", confirmed_by=3, confirmed_at=NOW, record_version=2))
        harness = Harness(db)
        yield harness
        db.rollback()
    engine.dispose()
