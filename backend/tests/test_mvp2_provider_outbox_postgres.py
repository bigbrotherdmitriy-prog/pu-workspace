"""Real PostgreSQL race and lease-fencing acceptance for product outbox."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register mapped tables
from app.api.tasks import ExternalActionApproval, approve_external
from app.jobs.queue import execution_owner
from app.mailbox_identity.service import MailboxIdentityService
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.models.v54_pilot import ConnectionIdentity
from app.models.v54_provider_action import (
    ProviderAction, ProviderActionApproval, ProviderDispatchOutbox,
    ProviderExecutionAttempt, ProviderOutcomeObservation,
)
from app.provider_actions.contracts import ProviderActionError, ProviderReceipt
from app.provider_actions.product import (
    RECONCILE_KIND, build_product_runtime, queue_confirmed_action,
    queue_reconciliation, resolve_reconciled_absence, run_product_reconcile_job,
    task_effect_states,
)
from app.provider_actions.runtime import PRODUCT_KIND
from app.schema import CURRENT_SCHEMA_REVISION


def _postgres_url():
    value = os.environ.get("PUW_TRACK_E_TEST_DSN")
    if not value:
        pytest.skip("isolated Track E PostgreSQL DSN is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1"}
    assert parsed.database == "puw_track_e_test" and not parsed.query
    return value


@pytest.fixture
def postgres_world():
    engine = create_engine(
        _postgres_url(), hide_parameters=True, pool_size=8,
        connect_args={"options": "-c statement_timeout=15000 -c lock_timeout=10000"},
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
    with sessions.begin() as db:
        org = Organization(name="Track E PostgreSQL race fixture")
        db.add(org); db.flush()
        user = User(name="Track E manager", email=f"track-e-{uuid4().hex}@example.invalid")
        db.add(user); db.flush()
        project = Project(name="Track E project", organization_id=org.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        token = GoogleOAuthToken(project_id=project.id, credential_generation=7,
                                 scopes="https://www.googleapis.com/auth/tasks https://www.googleapis.com/auth/calendar.events",
                                 access_token=None, refresh_token=None)
        db.add(token); db.flush()
        # These counters deliberately differ: token generation is not mailbox generation.
        MailboxIdentityService().bind_verified_google_subject(
            db, organization_id=org.id, google_token_id=token.id,
            subject=f"pg-subject-{project.id}",
            now=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        task = Task(project_id=project.id, assignee_user_id=user.id, created_by_user_id=user.id,
                    title="Track E race", description="Synthetic only", status="assigned",
                    due_date=date(2035, 1, 2), source_type="synthetic", source_file_id="track-e-race",
                    source_file_name="fixture", source_excerpt="synthetic fixture",
                    source_excerpt_hash="e" * 64, confidence=1, needs_review=False)
        db.add(task); db.flush()
        ids = (org.id, project.id, user.id, task.id)
    try:
        yield sessions, ids
    finally:
        engine.dispose()


def test_two_simultaneous_confirms_create_one_action_and_one_job(postgres_world):
    sessions, (org_id, _project_id, user_id, task_id) = postgres_world
    barrier = Barrier(2)

    def confirm():
        with sessions() as db:
            actor = db.get(User, user_id)
            barrier.wait(timeout=10)
            return queue_confirmed_action(db, action_kind="google.tasks.upsert",
                                          target_id=task_id, actor=actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm)
        second = pool.submit(confirm)
        outcomes = (first.result(timeout=25), second.result(timeout=25))

    assert outcomes[0]["job_id"] == outcomes[1]["job_id"]
    assert {item["already_queued"] for item in outcomes} == {False, True}
    with sessions() as db:
        actions = list(db.scalars(select(ProviderAction).where(
            ProviderAction.organization_id == org_id,
            ProviderAction.action_id == f"google-task-{task_id}",
        )))
        jobs = list(db.scalars(select(BackgroundJob).where(
            BackgroundJob.kind == PRODUCT_KIND,
            BackgroundJob.idempotency_key == actions[0].idempotency_key,
        )))
        assert len(actions) == len(jobs) == 1
        assert db.get(ProviderDispatchOutbox, (actions[0].action_id, 1)).job_id == jobs[0].id
        assert set(jobs[0].payload) == {"organization_id", "action_id", "revision"}


def test_approved_batch_retains_live_authority_after_queue_projection_on_postgresql(postgres_world):
    sessions, (org_id, _project_id, user_id, task_id) = postgres_world
    with sessions() as db:
        task = db.get(Task, task_id)
        approved = approve_external(
            task_id,
            ExternalActionApproval(expected_record_version=task.record_version,
                                   publish_task=True, publish_calendar=True),
            db=db, user=db.get(User, user_id),
        )
    assert len(approved["actions"]) == 2

    runtime = build_product_runtime(sessions=sessions)
    with sessions() as db:
        rows = list(db.scalars(select(ProviderAction).where(
            ProviderAction.organization_id == org_id,
            ProviderAction.action_id.in_((f"google-task-{task_id}", f"google-calendar-{task_id}")),
        )))
        assert len(rows) == 2
        for row in rows:
            assert row.state == "READY"
            authority = runtime.authority.resolve(runtime._envelope(row), operation="dispatch")
            assert authority.can_dispatch is True


def _claim(sessions, job_id: int, worker: str, attempt: int):
    now = datetime.now(timezone.utc)
    with sessions.begin() as db:
        job = db.get(BackgroundJob, job_id)
        job.status = "running"
        job.worker_id = worker
        job.attempts = attempt
        job.locked_at = now
        job.lease_expires_at = now + timedelta(minutes=5)
    return (job_id, worker, attempt, now)


def test_revoked_approval_cannot_dispatch_on_postgresql(postgres_world):
    sessions, (org_id, _project_id, user_id, task_id) = postgres_world
    with sessions() as db:
        queued = queue_confirmed_action(db, action_kind="google.tasks.upsert",
                                        target_id=task_id, actor=db.get(User, user_id))
    with sessions.begin() as db:
        approval = db.scalar(select(ProviderActionApproval).where(
            ProviderActionApproval.action_id == queued["action_id"],
        ))
        approval.state = "REVOKED"

    class NoProviderEffect:
        def __getattr__(self, _name):
            raise AssertionError("revoked action must not touch Google")

    runtime = build_product_runtime(sessions=sessions, service_factory=lambda *_: NoProviderEffect())
    with pytest.raises(ProviderActionError, match="dispatch_binding_mismatch"):
        runtime.execute_job(
            {"organization_id": org_id, "action_id": queued["action_id"], "revision": 1},
            _claim(sessions, queued["job_id"], "worker-revoked", 1),
        )


def test_crash_after_calendar_effect_uses_lookup_not_second_insert_on_postgresql(postgres_world):
    sessions, (org_id, _project_id, user_id, task_id) = postgres_world
    with sessions() as db:
        queued = queue_confirmed_action(db, action_kind="google.calendar.upsert",
                                        target_id=task_id, actor=db.get(User, user_id))

    class Request:
        def __init__(self, result, crash=False):
            self.result, self.crash = result, crash

        def execute(self):
            if self.crash:
                raise SystemExit("synthetic crash after external effect")
            return self.result

    class Calendar:
        def __init__(self):
            self.effects = 0
            self.crash = True
            self.items = {}

        def events(self):
            return self

        def insert(self, **kwargs):
            self.effects += 1
            item = {"id": kwargs["body"]["id"], **kwargs["body"]}
            self.items[item["id"]] = item
            return Request(item, self.crash)

        def get(self, **kwargs):
            return Request(self.items[kwargs["eventId"]])

    fake = Calendar()
    runtime = build_product_runtime(sessions=sessions, service_factory=lambda *_: fake)
    payload = {"organization_id": org_id, "action_id": queued["action_id"], "revision": 1}
    with pytest.raises(SystemExit):
        runtime.execute_job(payload, _claim(sessions, queued["job_id"], "worker-crashed", 1))
    fake.crash = False
    result = runtime.execute_job(payload, _claim(sessions, queued["job_id"], "worker-recovered", 2))
    assert result["outcome"] == "APPLIED" and fake.effects == 1


def _rotate_google(sessions, org_id, project_id):
    with sessions.begin() as db:
        token = db.scalar(select(GoogleOAuthToken).where(
            GoogleOAuthToken.project_id == project_id,
        ).with_for_update())
        token.credential_generation += 1
        MailboxIdentityService().bind_verified_google_subject(
            db, organization_id=org_id, google_token_id=token.id,
            subject=f"pg-subject-{project_id}",
        )


def _unknown_after_rotation(postgres_world):
    sessions, (org_id, project_id, user_id, task_id) = postgres_world
    with sessions() as db:
        queued = queue_confirmed_action(
            db, action_kind="google.tasks.upsert", target_id=task_id,
            actor=db.get(User, user_id),
        )

    def unavailable(*_args):
        raise RuntimeError("synthetic adapter outage")

    runtime = build_product_runtime(sessions=sessions, service_factory=unavailable)
    payload = {"organization_id": org_id, "action_id": queued["action_id"], "revision": 1}
    assert runtime.execute_job(payload, _claim(sessions, queued["job_id"], "initial", 1))["outcome"] == "UNKNOWN"
    with sessions.begin() as db:
        db.get(BackgroundJob, queued["job_id"]).status = "completed"
    _rotate_google(sessions, org_id, project_id)
    return SimpleNamespace(
        sessions=sessions, org_id=org_id, project_id=project_id, user_id=user_id,
        task_id=task_id, action_id=queued["action_id"], original_job_id=queued["job_id"],
        payload=payload,
    )


def _queue_check(world):
    with world.sessions() as db:
        return queue_reconciliation(
            db, action_id=world.action_id, revision=1, actor=db.get(User, world.user_id),
        )


def _run_check(world, owner):
    with execution_owner(owner[0], owner[1], attempt=owner[2], locked_at=owner[3]):
        return run_product_reconcile_job(world.payload)


def _original_seal(world):
    with world.sessions() as db:
        action = db.get(ProviderAction, (world.action_id, 1))
        approval = db.scalar(select(ProviderActionApproval).where(
            ProviderActionApproval.action_id == world.action_id,
            ProviderActionApproval.revision == 1,
        ))
        attempt = db.get(ProviderExecutionAttempt, (world.action_id, 1))
        return (
            action.envelope_hash, action.payload_hash, action.mailbox_key,
            action.command_key, action.idempotency_key, action.credential_generation,
            tuple(action.evidence_pins), action.created_at,
            approval.id, approval.envelope_hash, approval.state,
            approval.granted_at, approval.expires_at, attempt.attempt_id,
        )


def _observations(world):
    with world.sessions() as db:
        return list(db.execute(select(
            ProviderOutcomeObservation.sequence, ProviderOutcomeObservation.outcome,
            ProviderOutcomeObservation.source, ProviderOutcomeObservation.safe_code,
        ).where(
            ProviderOutcomeObservation.action_id == world.action_id,
            ProviderOutcomeObservation.revision == 1,
        ).order_by(ProviderOutcomeObservation.sequence)))


class _ReadOnlyTasks:
    """No external network; gates surround the provider read, not API request setup."""

    def __init__(self, rows=(), *, gated=False, crash=False):
        self.rows = list(rows)
        self.entered, self.release = Event(), Event()
        self.gated, self.crash = gated, crash
        self.reads = 0
        self.forbidden = []

    def tasks(self):
        return self

    def list(self, **_kwargs):
        def execute():
            self.reads += 1
            self.entered.set()
            if self.gated and not self.release.wait(timeout=15):
                raise AssertionError("provider gate timed out")
            if self.crash:
                self.crash = False
                raise SystemExit("synthetic crash after provider read")
            return {"items": self.rows}
        return SimpleNamespace(execute=execute)

    def __getattr__(self, name):
        self.forbidden.append(name)
        raise AssertionError("lookup must not mutate the provider")


def _lookup_runtime(world, fake, monkeypatch):
    runtime = build_product_runtime(sessions=world.sessions, service_factory=lambda *_args: fake)
    monkeypatch.setattr("app.provider_actions.product.build_product_runtime", lambda: runtime)
    return runtime


def _confirm_absence(world, sequence):
    with world.sessions() as db:
        return resolve_reconciled_absence(
            db, action_id=world.action_id, revision=1,
            expected_observation_sequence=sequence, confirmed_absent=True,
            actor=db.get(User, world.user_id),
        )


def _negative_check(world, monkeypatch):
    fake = _ReadOnlyTasks()
    runtime = _lookup_runtime(world, fake, monkeypatch)
    queued = _queue_check(world)
    owner = _claim(world.sessions, queued["job_id"], "lookup", 1)
    assert _run_check(world, owner)["outcome"] == "UNKNOWN"
    with world.sessions.begin() as db:
        db.get(BackgroundJob, queued["job_id"]).status = "completed"
    observation = _observations(world)[-1]
    assert observation.safe_code == "receipt_not_found"
    assert fake.reads == 1 and fake.forbidden == []
    return runtime, fake, observation.sequence


def _late_receipt(world):
    with world.sessions() as db:
        row = db.get(ProviderAction, (world.action_id, 1))
        return ProviderReceipt(
            action_id=row.action_id, revision=1, organization_id=row.organization_id,
            project_id=row.project_id, mailbox_key=row.mailbox_key,
            command_key=row.command_key, idempotency_key=row.idempotency_key,
            payload_hash=row.payload_hash, outcome="APPLIED", external_ref="synthetic-late-task",
        )


def test_concurrent_rotated_reconciliation_requests_create_one_job_on_postgresql(postgres_world):
    world = _unknown_after_rotation(postgres_world)
    before = _original_seal(world)
    barrier = Barrier(2)

    def request():
        with world.sessions() as db:
            actor = db.get(User, world.user_id)
            barrier.wait(timeout=10)
            return queue_reconciliation(db, action_id=world.action_id, revision=1, actor=actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request) for _ in range(2)]
        results = [future.result(timeout=25) for future in futures]
    assert results[0]["job_id"] == results[1]["job_id"]
    assert {result["already_queued"] for result in results} == {False, True}
    with world.sessions() as db:
        jobs = list(db.scalars(select(BackgroundJob).where(
            BackgroundJob.kind == RECONCILE_KIND,
            BackgroundJob.idempotency_key.like(f"provider-reconcile:{world.org_id}:{world.action_id}:1:%"),
        )))
        assert len(jobs) == 1 and jobs[0].payload == world.payload
        assert db.get(ProviderAction, (world.action_id, 1)).state == "UNKNOWN"
    assert len(_observations(world)) == 1 and _original_seal(world) == before


def test_concurrent_absence_resolution_and_reconfirmation_are_single_winner_on_postgresql(
    postgres_world, monkeypatch,
):
    world = _unknown_after_rotation(postgres_world)
    before = _original_seal(world)
    _runtime, fake, sequence = _negative_check(world, monkeypatch)
    barrier = Barrier(2)

    def resolve():
        with world.sessions() as db:
            actor = db.get(User, world.user_id)
            barrier.wait(timeout=10)
            try:
                return resolve_reconciled_absence(
                    db, action_id=world.action_id, revision=1,
                    expected_observation_sequence=sequence, confirmed_absent=True, actor=actor,
                )["status"]
            except ProviderActionError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(resolve) for _ in range(2)]
        assert sorted(future.result(timeout=25) for future in futures) == ["failed", "outcome_not_reconcilable"]
    assert [row.outcome for row in _observations(world)] == ["UNKNOWN", "UNKNOWN", "NOT_APPLIED"]
    with pytest.raises(ProviderActionError, match="outcome_not_reconcilable"):
        _confirm_absence(world, sequence)

    barrier = Barrier(2)

    def confirm():
        with world.sessions() as db:
            actor = db.get(User, world.user_id)
            barrier.wait(timeout=10)
            return queue_confirmed_action(db, action_kind="google.tasks.upsert", target_id=world.task_id,
                                          actor=actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm) for _ in range(2)]
        results = [future.result(timeout=25) for future in futures]
    assert {result["revision"] for result in results} == {2}
    assert len({result["job_id"] for result in results}) == 1
    assert {result["already_queued"] for result in results} == {False, True}
    assert _original_seal(world) == before and fake.forbidden == []
    with world.sessions() as db:
        assert db.get(ProviderAction, (world.action_id, 1)).state == "NOT_APPLIED"
        assert db.get(ProviderAction, (world.action_id, 3)) is None


@pytest.mark.parametrize("change", ["rotation", "identity_revoked", "lease_expired"])
@pytest.mark.parametrize("found", [False, True])
def test_lookup_result_is_fenced_when_account_or_claim_changes_on_postgresql(
    postgres_world, monkeypatch, change, found,
):
    world = _unknown_after_rotation(postgres_world)
    before = _original_seal(world)
    with world.sessions() as db:
        action = db.get(ProviderAction, (world.action_id, 1))
        from app.provider_actions.product import task_payload
        rows = [{"id": "synthetic-found", **task_payload(db.get(Task, world.task_id))}]
        rows[0]["notes"] += f"\nPU-Command: {sha256(action.idempotency_key.encode()).hexdigest()}"
    fake = _ReadOnlyTasks(rows if found else [], gated=True)
    _lookup_runtime(world, fake, monkeypatch)
    queued = _queue_check(world)
    owner = _claim(world.sessions, queued["job_id"], "gated-lookup", 1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_check, world, owner)
        try:
            assert fake.entered.wait(timeout=10)
            if change == "rotation":
                _rotate_google(world.sessions, world.org_id, world.project_id)
            else:
                with world.sessions.begin() as db:
                    if change == "identity_revoked":
                        identity = db.scalar(select(ConnectionIdentity).where(
                            ConnectionIdentity.organization_id == world.org_id,
                            ConnectionIdentity.account_key == f"pg-subject-{world.project_id}",
                        ))
                        identity.state = "revoked"
                        identity.binding_epoch += 1
                        identity.record_version += 1
                    else:
                        db.get(BackgroundJob, queued["job_id"]).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        finally:
            fake.release.set()
        with pytest.raises(ProviderActionError) as error:
            future.result(timeout=25)
        assert error.value.code in {"authority_stale", "dispatch_binding_mismatch"}
    assert fake.reads == 1 and fake.forbidden == []
    assert _original_seal(world) == before
    assert [row.outcome for row in _observations(world)] == ["UNKNOWN"]
    with world.sessions() as db:
        assert db.get(ProviderAction, (world.action_id, 1)).state == "UNKNOWN"
        assert db.get(Task, world.task_id).google_task_id is None
        assert task_effect_states(db, world.task_id)["task"]["can_confirm_absence"] is False


@pytest.mark.parametrize("checkpoint", ["after_read", "after_observation"])
def test_reconciliation_crash_retries_only_lookup_on_postgresql(postgres_world, monkeypatch, checkpoint):
    world = _unknown_after_rotation(postgres_world)
    before = _original_seal(world)
    fake = _ReadOnlyTasks(crash=checkpoint == "after_read")
    runtime = _lookup_runtime(world, fake, monkeypatch)
    queued = _queue_check(world)
    if checkpoint == "after_observation":
        record = runtime._record
        crashed = False

        def crash_after_record(*args, **kwargs):
            nonlocal crashed
            result = record(*args, **kwargs)
            if not crashed:
                crashed = True
                raise SystemExit("synthetic crash before job completion")
            return result

        monkeypatch.setattr(runtime, "_record", crash_after_record)
    with pytest.raises(SystemExit):
        _run_check(world, _claim(world.sessions, queued["job_id"], "crashed-check", 1))
    with world.sessions() as db:
        assert db.get(BackgroundJob, queued["job_id"]).status == "running"
    before_retry = _observations(world)
    assert len(before_retry) == (1 if checkpoint == "after_read" else 2)
    result = _run_check(world, _claim(world.sessions, queued["job_id"], "replacement-check", 2))
    assert result["outcome"] == "UNKNOWN"
    assert fake.reads == 2 and fake.forbidden == []
    after_retry = _observations(world)
    assert after_retry[:len(before_retry)] == before_retry
    # Identical account/authority pins make same-job replay idempotent.
    assert [row.outcome for row in after_retry] == ["UNKNOWN", "UNKNOWN"]
    assert after_retry[-1].safe_code == "receipt_not_found"
    assert _original_seal(world) == before


def test_late_applied_receipt_wins_over_inflight_empty_lookup_on_postgresql(postgres_world, monkeypatch):
    world = _unknown_after_rotation(postgres_world)
    fake = _ReadOnlyTasks(gated=True)
    runtime = _lookup_runtime(world, fake, monkeypatch)
    queued = _queue_check(world)
    owner = _claim(world.sessions, queued["job_id"], "empty-lookup", 1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_check, world, owner)
        try:
            assert fake.entered.wait(timeout=10)
            assert runtime.record_late_receipt(
                world.action_id, 1, _late_receipt(world), actor_id=str(world.user_id),
                correlation_id="late-positive-during-lookup",
            )["outcome"] == "APPLIED"
        finally:
            fake.release.set()
        try:
            result = future.result(timeout=25)
            assert result["outcome"] == "APPLIED"
        except ProviderActionError as exc:
            assert exc.code in {"authority_stale", "outcome_not_reconcilable"}
    assert [row.outcome for row in _observations(world)] == ["UNKNOWN", "APPLIED"]
    assert fake.forbidden == []
    with world.sessions() as db:
        state = task_effect_states(db, world.task_id)["task"]
        assert state["status"] == "applied" and state["can_confirm_absence"] is False


def test_late_original_applied_fences_concurrent_republication_dispatch_on_postgresql(
    postgres_world, monkeypatch,
):
    world = _unknown_after_rotation(postgres_world)
    before = _original_seal(world)
    runtime, fake, sequence = _negative_check(world, monkeypatch)
    _confirm_absence(world, sequence)
    with world.sessions() as db:
        republished = queue_confirmed_action(
            db, action_kind="google.tasks.upsert", target_id=world.task_id,
            actor=db.get(User, world.user_id),
        )
    assert republished["revision"] == 2
    owner = _claim(world.sessions, republished["job_id"], "new-dispatch", 1)
    late_written, release_late, dispatch_entered = Event(), Event(), Event()
    audit = runtime._audit
    dispatch_binding = runtime._dispatch_binding

    def gate_late_commit(db, event, action_id, revision, actor_id, correlation_id, **safe):
        audit(db, event, action_id, revision, actor_id, correlation_id, **safe)
        if event == "outcome_observed" and revision == 1 and safe.get("outcome") == "APPLIED":
            late_written.set()
            assert release_late.wait(timeout=15)

    def signal_dispatch_entry(*args, **kwargs):
        dispatch_entered.set()
        return dispatch_binding(*args, **kwargs)

    monkeypatch.setattr(runtime, "_audit", gate_late_commit)
    monkeypatch.setattr(runtime, "_dispatch_binding", signal_dispatch_entry)
    payload = {**world.payload, "revision": 2}
    with ThreadPoolExecutor(max_workers=2) as pool:
        late = pool.submit(
            runtime.record_late_receipt, world.action_id, 1, _late_receipt(world),
            actor_id=str(world.user_id), correlation_id="late-original-after-republication",
        )
        try:
            assert late_written.wait(timeout=10)
            dispatch = pool.submit(runtime.execute_job, payload, owner)
            assert dispatch_entered.wait(timeout=10)
            assert not dispatch.done()
        finally:
            release_late.set()
        assert late.result(timeout=25)["outcome"] == "APPLIED"
        with pytest.raises(ProviderActionError, match="unknown_requires_reconciliation"):
            dispatch.result(timeout=25)
    assert [row.outcome for row in _observations(world)] == ["UNKNOWN", "UNKNOWN", "NOT_APPLIED", "APPLIED"]
    assert fake.reads == 1 and fake.forbidden == [] and _original_seal(world) == before
    with world.sessions() as db:
        assert db.get(ProviderAction, (world.action_id, 1)).state == "APPLIED"
        assert db.get(ProviderAction, (world.action_id, 2)).state == "BLOCKED"
        assert db.get(ProviderExecutionAttempt, (world.action_id, 2)) is None
        with pytest.raises(ProviderActionError, match="unknown_requires_reconciliation"):
            queue_confirmed_action(db, action_kind="google.tasks.upsert", target_id=world.task_id,
                                   actor=db.get(User, world.user_id))
        db.rollback()
        assert db.get(ProviderAction, (world.action_id, 3)) is None
    with pytest.raises(ProviderActionError, match="outcome_not_reconcilable"):
        _confirm_absence(world, sequence)


@pytest.mark.parametrize("changed_row", ["approval", "task"])
def test_final_reconcile_validation_holds_authority_rows_until_commit_on_postgresql(
    postgres_world, monkeypatch, changed_row,
):
    world = _unknown_after_rotation(postgres_world)
    fake = _ReadOnlyTasks()
    runtime = _lookup_runtime(world, fake, monkeypatch)
    queued = _queue_check(world)
    owner = _claim(world.sessions, queued["job_id"], "final-cas", 1)
    validated, release_validation = Event(), Event()
    validate = runtime.authority.validate_reconcile_state

    def hold_after_validation(db, envelope, pins):
        validate(db, envelope, pins)
        validated.set()
        assert release_validation.wait(timeout=15)

    monkeypatch.setattr(runtime.authority, "validate_reconcile_state", hold_after_validation)
    if changed_row == "approval":
        mutation = update(ProviderActionApproval).where(
            ProviderActionApproval.action_id == world.action_id,
            ProviderActionApproval.revision == 1,
        ).values(state="REVOKED")
    else:
        mutation = update(Task).where(Task.id == world.task_id).values(
            title="Synthetic task edited after readback",
            record_version=Task.record_version + 1,
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_check, world, owner)
        try:
            assert validated.wait(timeout=10)
            # A distinct DB transaction attempts the real UPDATE after validation
            # returned but before its observation can commit. PostgreSQL, not a
            # Python mutex, must prevent the authority/payload from changing here.
            with pytest.raises(OperationalError) as error:
                with world.sessions.begin() as db:
                    db.execute(text("SET LOCAL lock_timeout = '250ms'"))
                    db.execute(mutation)
            sqlstate = getattr(error.value.orig, "sqlstate", None) or getattr(error.value.orig, "pgcode", None)
            assert sqlstate == "55P03"  # lock_not_available, not an unrelated SQL failure
            assert not future.done()
            assert len(_observations(world)) == 1
        finally:
            release_validation.set()
        assert future.result(timeout=25)["outcome"] == "UNKNOWN"

    observed = _observations(world)
    assert [item.outcome for item in observed] == ["UNKNOWN", "UNKNOWN"]
    assert observed[-1].safe_code == "receipt_not_found"
    assert fake.reads == 1 and fake.forbidden == []
    # Once the observation commits the row lock must be released, so this normal
    # edit/revocation succeeds and invalidates the earlier absence permission.
    with world.sessions.begin() as db:
        db.execute(text("SET LOCAL lock_timeout = '1s'"))
        assert db.execute(mutation).rowcount == 1
    with pytest.raises(ProviderActionError) as error:
        _confirm_absence(world, observed[-1].sequence)
    assert error.value.code in {"authority_stale", "outcome_not_reconcilable"}
    assert _observations(world) == observed
