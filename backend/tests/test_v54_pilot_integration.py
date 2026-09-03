"""Real A/B/C composition; SQLite tests are not a PostgreSQL fault proof."""
from pathlib import Path

import json
import os
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, select, func, text
from sqlalchemy.orm import sessionmaker

import app.models
from app.database import Base
from app.core.v54_dto import ActionEnvelope, DeadlineClaimInput, canonical_json
from app.core.v54_interfaces import ContextConfirmation, ReviewCommand
from app.core.v54_refs import ObjectRef, VersionPin
from app.integrations.connection_identity import IdentityFacade
from app.models.organization_contract import Organization, Contract
from app.models.project import Project
from app.models.user import User
from app.models.task import Task, TaskHistory
from app.models.management import Obligation
from app.models.job import BackgroundJob
from app.models.v54_pilot import ActionPolicy, ActionReceipt, PendingDispatch, PilotAction, ContextRelation
from app.jobs.queue import claim
from v54_pilot_fixture import NOW, DOC_FIXTURE, envelopes, ref, pin, uid
from test_v54_source_evidence_pilot import policy, scope


def vp(kind, value, version=1, version_kind="revision"):
    return VersionPin.model_validate(pin(kind, value, version, version_kind=version_kind))


@pytest.fixture
def integrated(tmp_path):
    pg_url = os.getenv("PUW_V54_INTEGRATION_DATABASE_URL")
    if pg_url:
        from sqlalchemy.engine import make_url
        from uuid import uuid4
        parsed = make_url(pg_url)
        assert parsed.get_backend_name() == "postgresql"
        assert parsed.host in {"localhost", "127.0.0.1", "::1", "db"} or (
            os.getenv("GITHUB_ACTIONS") == "true" and parsed.host == "postgres")
        assert (parsed.database or "").startswith("puw_v54_test_") and not parsed.query
        schema = "v54_integration_" + uuid4().hex
        admin = create_engine(pg_url, hide_parameters=True, connect_args={"connect_timeout": 5})
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(pg_url, hide_parameters=True, connect_args={"connect_timeout": 5,
            "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000"})
        engine.v54_test_schema = schema
    else:
        engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic.db'}")
        @event.listens_for(engine, "connect")
        def fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield seed_composition(engine)
    finally:
        engine.dispose()
        if pg_url:
            # Exact random schema, even when setup/seed fails before yield.
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def seed_composition(engine):
    from app.pilot_composition import SyntheticComposition
    from app.pilot_dispatch import SyntheticDispatch
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    pol = policy()
    grants = {(actor, op) for actor in (2, 3) for op in (
        "metadata", "review", "dispatch", "audit", "audit.append", "action.receipt.read")}
    grants.update((2, op) for op in ("identity", "write", "observe", "mailbox.bootstrap",
        "claim.extract", "action.freeze", "action.dispatch", "action.execute", "task.assign"))
    grants.update((3, op) for op in ("claim.review", "action.approve", "action.revoke", "task.assign"))
    grants.add((2, "task.assignee"))
    pol = replace(pol, grants=frozenset(grants))
    with sessions.begin() as db:
        db.add(Organization(id=1, name="Synthetic only"))
        db.add_all([User(id=2, name="Requester", email="requester@example.test"),
                    User(id=3, name="Reviewer", email="reviewer@example.test")])
        db.flush()
        db.add(Project(id=4, name="Synthetic intake", organization_id=1))
        db.flush()
        db.add(Contract(id=5, project_id=4, number="TEST", title="Synthetic contract"))
        rules = next(r for r in json.loads(DOC_FIXTURE.read_text(encoding="utf8"))["records"] if r["ref"]["type"] == "policy")
        db.add(ActionPolicy(id=uid(22), revision=1, organization_id=1,
            policy_hash=envelopes()[0]["policy_sha256"], mode="CONFIRM", scope_ref=ref("project", 4),
            rules=rules, valid_until=NOW + timedelta(minutes=5)))
        db.flush()
        identity = IdentityFacade(pol, lambda: NOW).register(db, scope=scope(), account_key="synthetic-account")
    pol = replace(pol, binding_epochs=((identity.id.value, 1),))
    component = SyntheticComposition(policy=pol, clock=lambda: NOW, enabled=True)
    runtime = SyntheticDispatch(sessions=sessions, composition_for_scope=lambda s: component)
    return sessions, component, runtime, identity


def prepare(integrated):
    from app.pilot_dispatch import synthetic_command_key
    sessions, c, runtime, identity = integrated
    with sessions.begin() as db:
        context = c.context(db, scope())
        mailbox = context.bootstrap_mail_connection(db, scope=scope(),
            identity=VersionPin(ref=identity, version_kind="record_version", value=1), namespace="synthetic-mailbox")
        source = c.source.register_source(db, scope=scope(), identity=identity, namespace="synthetic-mailbox",
            external_id="synthetic-integration-message", object_kind="message")
        source, version = c.source.observe(db, scope=scope(), source=source, identity=identity,
            namespace="synthetic-mailbox", observation_key="observation-message", provider_revision="v1")
        attachment = c.source.register_source(db, scope=scope(), identity=identity, namespace="synthetic-mailbox",
            external_id="synthetic-integration-attachment", object_kind="attachment", parent=source.ref)
        attachment, attached_version = c.source.observe(db, scope=scope(), source=attachment, identity=identity,
            namespace="synthetic-mailbox", observation_key="observation-attachment", provider_revision="v1")
        evidence = c.source.create_evidence(db, scope=scope(), source=attachment.ref, version=attached_version, evidence_id=uid(160))
        c.source.review(db, scope=scope(3), command=ReviewCommand(subject=evidence, expected_record_version=1, decision="confirmed"))
        message = context.register(db, scope=scope(), mailbox=mailbox, source=source, attachment=attachment)
        assert context.register(db, scope=scope(), mailbox=mailbox, source=source, attachment=attachment) == message
        relations = context.propose(db, scope=scope(), message=message, expected_context_version=1,
            project=vp("project", 4, version_kind="record_version"), contract=vp("contract", 5, version_kind="record_version"), evidence=(evidence,))
        context.confirm(db, scope=scope(), command=ContextConfirmation(message=message,
            project_relation=relations[0], contract_relation=relations[1], expected_context_version=1,
            expected_project_relation_record_version=1, expected_contract_relation_record_version=1))
        claim_pin = c.claims.extract(db, scope=scope(), claim=DeadlineClaimInput(anchor=ref("deadline_claim", uid(161)),
            revision=1, message=message, due_date="2026-09-10", timezone="Europe/Moscow", evidence=(evidence,)))
        c.claims.review(db, scope=scope(3), command=ReviewCommand(subject=claim_pin, expected_record_version=1, decision="confirmed"))
        data = envelopes()[0]
        data.update(action_ref=ref("action", uid(162)), claim=claim_pin.model_dump(mode="json"),
            evidence=[evidence.model_dump(mode="json")], expected_context_version=2,
            connection_ref=identity.model_dump(mode="json"),
            idempotency_key=synthetic_command_key(ObjectRef.model_validate(ref("action", uid(162))), 1))
        data["source_versions"] = sorted([p.model_dump(mode="json") for p in (version, attached_version)], key=canonical_json)
        data["relations"] = sorted([p.model_dump(mode="json") for p in relations], key=canonical_json)
        envelope = ActionEnvelope.model_validate(data)
        action = context.handoff(db, scope=scope(), message=message, envelope=envelope, trust=c.trust)
        approve_dispatch(db, c, envelope, action)
    return envelope


def approve_dispatch(db, c, envelope, action):
    from app.core.v54_dto import canonical_hash
    approval = c.trust.approve(db, scope=scope(3), action=action,
        envelope_hash=canonical_hash(envelope.model_dump(mode="json")), command_key="approval-" + envelope.idempotency_key,
        expires_at=NOW + timedelta(minutes=4))
    c.trust.request_dispatch(db, scope=scope(), action=action, approval=approval,
        expected_record_version=db.get(PilotAction, action.ref.id.value).record_version)


def execute(integrated, envelope):
    sessions, c, runtime, _ = integrated
    job_id = runtime.enqueue_action(envelope.action_ref.id.value, uid(999))
    with sessions() as db:
        job = claim(db, "synthetic-worker", 300)
        assert job.id == job_id
        owner = (job.id, job.worker_id, job.attempts, job.locked_at)
        payload = job.payload
    result = runtime.execute(payload, owner)
    return result, payload, owner


def test_real_abc_task_receipt_projection_and_separate_cancel(integrated):
    from app.pilot_dispatch import synthetic_command_key
    sessions, c, runtime, _ = integrated
    envelope = prepare(integrated)
    result, payload, owner = execute(integrated, envelope)
    assert runtime.execute(payload, owner) == result
    with sessions.begin() as db:
        task = db.scalar(select(Task))
        assert task.message_id and task.status == "assigned" and task.source_type == "v54_synthetic"
        assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 1
        assert db.scalar(select(func.count()).select_from(Obligation)) == 0
        assert db.scalar(select(func.count()).select_from(ContextRelation).where(ContextRelation.receipt_id.is_not(None))) == 1
        cancel_data = envelopes()[1]
        for key in ("claim", "evidence", "source_versions", "relations", "expected_context_version", "connection_ref"):
            cancel_data[key] = envelope.model_dump(mode="json")[key]
        cancel_data.update(action_ref=ref("action", uid(163)), compensates_action_ref=envelope.action_ref.model_dump(mode="json"),
            target=pin("task", task.id, version_kind="record_version"),
            idempotency_key=synthetic_command_key(ObjectRef.model_validate(ref("action", uid(163))), 1))
        cancel = ActionEnvelope.model_validate(cancel_data)
        action = c.trust.freeze(db, scope=scope(), envelope=cancel)
        approve_dispatch(db, c, cancel, action)
    execute(integrated, cancel)
    with sessions() as db:
        assert db.scalar(select(Task)).status == "cancelled"
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(TaskHistory)) == 2
        assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 2


def test_disabled_runtime_cannot_enqueue_and_preserves_intent(integrated):
    sessions, c, runtime, _ = integrated
    envelope = prepare(integrated)
    c.enabled = False
    with pytest.raises(ValueError):
        runtime.enqueue_action(envelope.action_ref.id.value, uid(999))
    with sessions() as db:
        assert db.get(PendingDispatch, envelope.action_ref.id.value).pending
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0


def test_t1_recovery_uses_existing_queue_key(integrated):
    sessions, c, runtime, _ = integrated
    envelope = prepare(integrated)
    assert runtime.recover() == 1
    with sessions.begin() as db:
        pending = db.get(PendingDispatch, envelope.action_ref.id.value)
        first = pending.job_id
        pending.job_id = None  # Simulated crash after enqueue before marker commit.
    assert runtime.recover() == 1
    with sessions() as db:
        assert db.get(PendingDispatch, envelope.action_ref.id.value).job_id == first
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 1


def claimed(integrated):
    sessions, c, runtime, identity = integrated
    envelope = prepare(integrated)
    runtime.enqueue_action(envelope.action_ref.id.value, uid(999))
    with sessions() as db:
        job = claim(db, "worker-one", 300)
        return envelope, job.payload, (job.id, job.worker_id, job.attempts, job.locked_at)


def test_worker_can_recover_enqueue_without_marker(integrated):
    sessions, c, runtime, _ = integrated
    envelope, payload, owner = claimed(integrated)
    with sessions.begin() as db:
        db.get(PendingDispatch, envelope.action_ref.id.value).job_id = None
    assert runtime.execute(payload, owner)["receipt_id"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1


@pytest.mark.parametrize("fault", ["attempt", "worker", "lease", "disabled", "revoked_identity", "payload", "context"])
def test_pre_dispatch_faults_have_no_task(integrated, fault):
    from app.models.ai_secretary import Message
    sessions, c, runtime, identity = integrated
    envelope, payload, owner = claimed(integrated)
    if fault == "attempt":
        owner = (*owner[:2], owner[2] + 1, owner[3])
    elif fault == "worker":
        owner = (owner[0], "stale-worker", *owner[2:])
    elif fault == "disabled":
        c.enabled = False
    elif fault == "payload":
        payload = {**payload, "content": "SYNTHETIC_MUST_NOT_ENTER_QUEUE"}
    else:
        with sessions.begin() as db:
            if fault == "lease":
                db.get(BackgroundJob, owner[0]).lease_expires_at = NOW - timedelta(seconds=1)
            elif fault == "context":
                db.scalar(select(Message)).context_version += 1
            else:
                IdentityFacade(c.policy, lambda: NOW).revoke(db, scope=scope(), identity=identity, expected_version=1)
    with pytest.raises(ValueError):
        runtime.execute(payload, owner)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 0


def test_audit_failure_rolls_back_task_receipt_and_reservation(integrated, monkeypatch):
    from app.core import v54_transactions
    sessions, c, runtime, _ = integrated
    envelope, payload, owner = claimed(integrated)
    original = v54_transactions.append_audit
    def fail_success(db, *, scope, event, authorize):
        if event.event == "ACTION_SUCCEEDED":
            raise RuntimeError("synthetic_audit_failure")
        return original(db, scope=scope, event=event, authorize=authorize)
    monkeypatch.setattr(v54_transactions, "append_audit", fail_success)
    with pytest.raises(RuntimeError, match="synthetic_audit_failure"):
        runtime.execute(payload, owner)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(TaskHistory)) == 0
        assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 0
        assert db.get(PilotAction, envelope.action_ref.id.value).business_state == "READY"
    monkeypatch.setattr(v54_transactions, "append_audit", original)
    assert runtime.execute(payload, owner)["receipt_id"]


def test_crash_after_t2_commit_replays_receipt_not_task(integrated, monkeypatch):
    from app.context_communication.service import ContextCommunication
    sessions, c, runtime, _ = integrated
    envelope, payload, owner = claimed(integrated)
    original = ContextCommunication.project_receipt
    def fail_consume(*args, **kwargs):
        raise RuntimeError("synthetic_consumer_crash")
    monkeypatch.setattr(ContextCommunication, "project_receipt", fail_consume)
    with pytest.raises(RuntimeError, match="synthetic_consumer_crash"):
        runtime.execute(payload, owner)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 1
    monkeypatch.setattr(ContextCommunication, "project_receipt", original)
    assert runtime.execute(payload, owner)["receipt_id"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(ContextRelation).where(ContextRelation.receipt_id.is_not(None))) == 1


def test_real_handler_requires_claim_snapshot(integrated):
    from app.jobs.handlers import run
    from app.jobs.queue import execution_owner
    from app.pilot_dispatch import install_synthetic_runtime
    _, _, runtime, _ = integrated
    _, payload, owner = claimed(integrated)
    install_synthetic_runtime(runtime)
    try:
        with execution_owner(owner[0], owner[1]):
            with pytest.raises(ValueError, match="pilot_worker_binding_required"):
                run("v54.synthetic_task", payload)
        with execution_owner(owner[0], owner[1], attempt=owner[2], locked_at=owner[3]):
            assert run("v54.synthetic_task", payload)["receipt_id"]
    finally:
        install_synthetic_runtime(None)


def test_bootstrap_rejects_namespace_outside_explicit_policy(integrated):
    sessions, c, _, identity = integrated
    with pytest.raises(ValueError):
        with sessions.begin() as db:
            c.context(db, scope()).bootstrap_mail_connection(db, scope=scope(),
                identity=VersionPin(ref=identity, version_kind="record_version", value=1), namespace="unapproved")


def test_db_helper_does_not_commit_or_call_legacy_executor():
    import ast
    import inspect
    import app.pilot_task_mutation as module
    tree = ast.parse(inspect.getsource(module))
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls.intersection({"commit", "rollback", "close", "enqueue", "publish_actions", "send_message"})


def test_queue_key_collision_does_not_link_other_job(integrated):
    from app.jobs.queue import enqueue
    sessions, _, runtime, _ = integrated
    envelope = prepare(integrated)
    with sessions() as db:
        enqueue(db, "another.synthetic.kind", {"id": 1}, idempotency_key=envelope.idempotency_key)
    with pytest.raises(ValueError, match="pilot_queue_key_conflict"):
        runtime.enqueue_action(envelope.action_ref.id.value, uid(999))
    with sessions() as db:
        assert db.get(PendingDispatch, envelope.action_ref.id.value).job_id is None
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_postgres_probe_requires_explicit_database_and_safe_failure():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k != "PUW_V54_INTEGRATION_DATABASE_URL"}
    result = subprocess.run([sys.executable, str(root / "scripts/ci/v54_pilot_runtime.py")],
        env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "AssertionError" in result.stdout
    assert not result.stderr and "Traceback" not in result.stdout


def test_runtime_schema_expectations_follow_foundation():
    from app.schema import CURRENT_SCHEMA_REVISION
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/docker-smoke.yml").read_text(encoding="utf8")
    harness = (root / "scripts/ci/durable_queue/run.py").read_text(encoding="utf8")
    assert f"schema.get('message') == '{CURRENT_SCHEMA_REVISION}'" in workflow
    assert f'b"{CURRENT_SCHEMA_REVISION}"' in harness


def test_real_task_mutation_is_available():
    from app.pilot_task_mutation import InternalTaskMutation
    assert callable(InternalTaskMutation.apply)
