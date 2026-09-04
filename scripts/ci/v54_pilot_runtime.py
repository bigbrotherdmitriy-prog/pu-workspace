"""Opt-in PostgreSQL process-fault probe. Never falls back to SQLite.

Run from repo root with backend dependencies and an explicitly isolated test DB:
  PUW_V54_INTEGRATION_DATABASE_URL=... python scripts/ci/v54_pilot_runtime.py
Outputs IDs/counts only. No DSN, exception text, envelopes or document data.
This does NOT prove production authority, migration correctness or external once.
"""
import multiprocessing as mp
import json
import os
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
CHECKPOINT = "require_database"


def checkpoint(value):
    global CHECKPOINT
    CHECKPOINT = value


def spawn_policy(policy):
    """Return the policy facts that are safe to serialize into a spawn child.

    Authority is reconstructed in the child from the same DB-backed resolver.  Its
    test clock is intentionally a local callable and therefore must never cross a
    multiprocessing spawn boundary.
    """
    return replace(policy, authority=None)


def child_runtime(url, schema, policy):
    """Rebuild the synthetic composition from DB-backed state in a spawn child."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.v54_authority import AuthorityResolver
    from app.pilot_composition import SyntheticComposition
    from app.pilot_dispatch import SyntheticDispatch, install_synthetic_runtime
    from v54_pilot_fixture import NOW

    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5,
        "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000"})
    sessions = sessionmaker(engine, expire_on_commit=False)
    policy = replace(policy, authority=AuthorityResolver(clock=lambda: NOW))
    component = SyntheticComposition(policy=policy, clock=lambda: NOW, enabled=True)
    runtime = SyntheticDispatch(sessions=sessions, composition_for_scope=lambda s: component)
    install_synthetic_runtime(runtime)
    return engine, sessions, component, runtime


def cleanup_probe(children, fixture):
    try:
        for child in children:
            # A failed Process.start() leaves pid unset; join() would raise and
            # mask the original spawn error with "can only join a started process".
            if getattr(child, "pid", "unknown") is None:
                continue
            if child.is_alive():
                child.terminate()
            child.join(10)
        fixture.close()
    except Exception:
        checkpoint("cleanup")
        raise


def contender(url, schema, policy, output, stop, pause_after_claim):
    from app.jobs.queue import claim, execution_owner, succeed
    from app.jobs.handlers import run
    engine, sessions, _component, _runtime = child_runtime(url, schema, policy)
    try:
        worker = "synthetic-process-" + str(os.getpid())
        with sessions() as db:
            job = claim(db, worker, 60)
        if job is None:
            output.put({"state": "no_claim"})
            return
        owner = (job.id, worker, job.attempts, job.locked_at)
        output.put({"state": "claimed", "owner": owner, "payload": job.payload})
        if pause_after_claim:
            stop.wait(30)
            return
        with execution_owner(job.id, worker, attempt=job.attempts, locked_at=job.locked_at):
            result = run(job.kind, job.payload)
        with sessions() as db:
            assert succeed(db, job.id, worker, result)
        output.put({"state": "completed", "job_id": job.id, "receipt_id": result["receipt_id"]})
    except Exception as exc:
        output.put({"state": "failed", "error_type": type(exc).__name__})
    finally:
        engine.dispose()


def intent_producer(url, schema, policy, identity, output, stop):
    """Commit T1 in a process which is then killed before it can enqueue."""
    engine, sessions, component, runtime = child_runtime(url, schema, policy)
    try:
        from test_v54_pilot_integration import prepare
        envelope = prepare((sessions, component, runtime, identity))
        output.put({"state": "t1_committed", "action_id": envelope.action_ref.id.value,
                    "revision": envelope.revision})
        # The parent terminates this exact child at the declared fault boundary.
        stop.wait(30)
    except Exception as exc:
        output.put({"state": "failed", "error_type": type(exc).__name__})
    finally:
        engine.dispose()


def t2_contender(url, schema, policy, output, stop, boundary):
    """Pause either inside T2 before commit or after T2 commit before job success."""
    from sqlalchemy import event
    from app.jobs.queue import claim, execution_owner
    from app.jobs.handlers import run
    from app.models.v54_pilot import ActionReceipt

    engine, sessions, _component, _runtime = child_runtime(url, schema, policy)
    armed = {"value": boundary == "before_commit"}

    if armed["value"]:
        @event.listens_for(sessions, "after_flush")
        def pause_before_commit(db, _context):
            if armed["value"] and any(isinstance(row, ActionReceipt) for row in db.new):
                armed["value"] = False
                output.put({"state": "t2_flushed"})
                # The outer sessions.begin() has not committed. A hard process
                # termination must make PostgreSQL roll the whole T2 back.
                stop.wait(30)
    try:
        worker = "synthetic-t2-" + str(os.getpid())
        with sessions() as db:
            job = claim(db, worker, 60)
        if job is None:
            output.put({"state": "no_claim"})
            return
        owner = (job.id, worker, job.attempts, job.locked_at)
        with execution_owner(job.id, worker, attempt=job.attempts, locked_at=job.locked_at):
            result = run(job.kind, job.payload)
        if boundary == "after_commit":
            output.put({"state": "business_committed", "job_id": job.id,
                        "receipt_id": result["receipt_id"]})
            # Deliberately do not call queue.succeed before parent termination.
            stop.wait(30)
        else:
            output.put({"state": "unexpected_return"})
    except Exception as exc:
        output.put({"state": "failed", "error_type": type(exc).__name__})
    finally:
        engine.dispose()


def terminate_at_boundary(child):
    """Terminate only a started synthetic child and prove it stopped."""
    if child.pid is None:
        raise AssertionError("fault_process_not_started")
    child.terminate()
    child.join(10)
    if child.is_alive():
        raise AssertionError("fault_process_survived")


def expire_and_recover(sessions, job_id):
    from datetime import timedelta
    from app.jobs.queue import recover_expired
    from app.models.job import BackgroundJob
    from v54_pilot_fixture import NOW

    with sessions.begin() as db:
        db.get(BackgroundJob, job_id).lease_expires_at = NOW - timedelta(seconds=1)
    with sessions() as db:
        assert recover_expired(db) == 1


def counts(sessions, job_id):
    """Return allowlisted business counters only; never serialize source content."""
    from sqlalchemy import func, select
    from app.models.audit_log import AuditLog
    from app.models.job import BackgroundJob
    from app.models.task import Task, TaskHistory
    from app.models.v54_pilot import ActionReceipt, AuditExtension, ContextRelation

    with sessions() as db:
        return {
            "tasks": db.scalar(select(func.count()).select_from(Task)),
            "history": db.scalar(select(func.count()).select_from(TaskHistory)),
            "receipts": db.scalar(select(func.count()).select_from(ActionReceipt)),
            "projections": db.scalar(select(func.count()).select_from(ContextRelation)
                .where(ContextRelation.receipt_id.is_not(None))),
            "success_audits": db.scalar(select(func.count()).select_from(AuditExtension)
                .join(AuditLog, AuditLog.id == AuditExtension.audit_log_id)
                .where(AuditLog.action == "v54.ACTION_SUCCEEDED")),
            "job_status": db.get(BackgroundJob, job_id).status,
        }


def complete_with_second_worker(ctx, state, output, stop, children, job_id):
    sessions, component, _runtime, _identity = state
    engine = sessions.kw["bind"]
    args = (os.environ["PUW_V54_INTEGRATION_DATABASE_URL"], engine.v54_test_schema,
            spawn_policy(component.policy), output, stop)
    second = ctx.Process(target=contender, args=(*args, False))
    second.start()
    children.append(second)
    claimed = output.get(timeout=25)
    assert claimed["state"] == "claimed"
    completed = output.get(timeout=25)
    assert completed["state"] == "completed" and completed["job_id"] == job_id
    second.join(10)
    assert second.exitcode == 0
    return completed


def main():
    checkpoint("require_database")
    assert os.getenv("PUW_V54_INTEGRATION_DATABASE_URL"), "Explicit isolated PostgreSQL URL required"
    from test_v54_pilot_integration import integrated, prepare
    from app.models.job import BackgroundJob
    from app.models.v54_pilot import PendingDispatch
    from sqlalchemy import func, select
    from v54_pilot_fixture import uid
    ctx = mp.get_context("spawn")

    # Existing lease-reclaim proof: a claimed job is not simultaneously claimed,
    # and a stale owner cannot execute after recovery.
    children = []
    with tempfile.TemporaryDirectory(prefix="puw-v54-pg-") as directory:
        checkpoint("fixture_setup")
        fixture = integrated.__wrapped__(Path(directory))
        state = next(fixture)
        sessions, component, runtime, _ = state
        engine = sessions.kw["bind"]
        assert engine.dialect.name == "postgresql"
        try:
            checkpoint("prepare")
            envelope = prepare(state)
            checkpoint("enqueue")
            job_id = runtime.enqueue_action(envelope.action_ref.id.value, uid(999))
            output, stop = ctx.Queue(), ctx.Event()
            args = (os.environ["PUW_V54_INTEGRATION_DATABASE_URL"], engine.v54_test_schema,
                    spawn_policy(component.policy), output, stop)
            first = ctx.Process(target=contender, args=(*args, True))
            first.start()
            children.append(first)
            checkpoint("first_claim")
            claimed = output.get(timeout=25)
            assert claimed["state"] == "claimed"
            rival = ctx.Process(target=contender, args=(*args, False))
            rival.start()
            children.append(rival)
            checkpoint("rival_no_claim")
            assert output.get(timeout=25)["state"] == "no_claim"
            rival.join(10)
            assert rival.exitcode == 0
            # Kill only the test process created immediately above; not Docker or prod.
            terminate_at_boundary(first)
            checkpoint("first_terminated")
            checkpoint("lease_recovery")
            expire_and_recover(sessions, job_id)
            checkpoint("stale_owner_rejected")
            try:
                runtime.execute(claimed["payload"], claimed["owner"])
            except ValueError:
                pass
            else:
                raise AssertionError("stale worker accepted before recovery")
            checkpoint("second_claim")
            completed = complete_with_second_worker(ctx, state, output, stop, children, job_id)
            checkpoint("second_completion")
            result = counts(sessions, job_id)
            checkpoint("invariants")
            assert all(result[name] == 1 for name in (
                "tasks", "history", "receipts", "projections", "success_audits"))
            assert result["job_status"] == "completed"
            print(json.dumps({"probe": "process_reclaim", "job_id": job_id, "receipt_id": completed["receipt_id"],
                   "status": "PASS", "expiry": "accelerated", "external_effects": "not_tested",
                   **result}))
        finally:
            cleanup_probe(children, fixture)

    # S07: the process which committed T1 is killed before enqueue. The restarted
    # reconciler must find the row without relying on process memory.
    children = []
    with tempfile.TemporaryDirectory(prefix="puw-v54-s07-") as directory:
        checkpoint("s07_fixture_setup")
        fixture = integrated.__wrapped__(Path(directory))
        state = next(fixture)
        sessions, component, runtime, identity = state
        engine = sessions.kw["bind"]
        output, stop = ctx.Queue(), ctx.Event()
        args = (os.environ["PUW_V54_INTEGRATION_DATABASE_URL"], engine.v54_test_schema,
                spawn_policy(component.policy), identity, output, stop)
        try:
            producer = ctx.Process(target=intent_producer, args=args)
            producer.start()
            children.append(producer)
            checkpoint("s07_t1_committed")
            committed = output.get(timeout=25)
            assert committed["state"] == "t1_committed"
            terminate_at_boundary(producer)
            checkpoint("s07_producer_terminated")
            with sessions() as db:
                pending = db.get(PendingDispatch, committed["action_id"])
                assert pending is not None and pending.pending and pending.job_id is None
                assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0
            checkpoint("s07_recover_enqueue")
            assert runtime.recover() == 1
            with sessions() as db:
                job_id = db.get(PendingDispatch, committed["action_id"]).job_id
                assert job_id is not None
            checkpoint("s07_second_worker")
            completed = complete_with_second_worker(ctx, state, output, stop, children, job_id)
            result = counts(sessions, job_id)
            assert all(result[name] == 1 for name in (
                "tasks", "history", "receipts", "projections", "success_audits"))
            assert result["job_status"] == "completed" and runtime.recover() == 0
            print(json.dumps({"probe": "s07_intent_recovery", "case": "S07", "status": "PASS",
                "process_kill": True, "pending_before_recovery": 1,
                "jobs_before_recovery": 0, "jobs_after_recovery": 1,
                "receipt_id": completed["receipt_id"], **result}))
        finally:
            cleanup_probe(children, fixture)

    # Additional fail-closed proof requested by the runtime contract: terminate
    # after Task/history/receipt have flushed but before the T2 transaction commits.
    children = []
    with tempfile.TemporaryDirectory(prefix="puw-v54-t2-rollback-") as directory:
        checkpoint("t2_rollback_fixture_setup")
        fixture = integrated.__wrapped__(Path(directory))
        state = next(fixture)
        sessions, component, runtime, _identity = state
        engine = sessions.kw["bind"]
        output, stop = ctx.Queue(), ctx.Event()
        try:
            envelope = prepare(state)
            job_id = runtime.enqueue_action(envelope.action_ref.id.value, uid(999))
            args = (os.environ["PUW_V54_INTEGRATION_DATABASE_URL"], engine.v54_test_schema,
                    spawn_policy(component.policy), output, stop, "before_commit")
            worker = ctx.Process(target=t2_contender, args=args)
            worker.start()
            children.append(worker)
            checkpoint("t2_flushed_before_commit")
            assert output.get(timeout=25)["state"] == "t2_flushed"
            terminate_at_boundary(worker)
            checkpoint("t2_uncommitted_worker_terminated")
            rolled_back = counts(sessions, job_id)
            assert all(rolled_back[name] == 0 for name in (
                "tasks", "history", "receipts", "projections", "success_audits"))
            assert rolled_back["job_status"] == "running"
            expire_and_recover(sessions, job_id)
            completed = complete_with_second_worker(ctx, state, output, stop, children, job_id)
            result = counts(sessions, job_id)
            assert all(result[name] == 1 for name in (
                "tasks", "history", "receipts", "projections", "success_audits"))
            assert result["job_status"] == "completed"
            print(json.dumps({"probe": "t2_precommit_rollback", "case": "S08-precommit",
                "status": "PASS", "process_kill": True, "uncommitted_tasks": rolled_back["tasks"],
                "uncommitted_receipts": rolled_back["receipts"],
                "receipt_id": completed["receipt_id"], **result}))
        finally:
            cleanup_probe(children, fixture)

    # S08: T2 commits but the worker dies before queue.succeed. Reclaiming the
    # same job must replay the receipt and never invoke Task mutation again.
    children = []
    with tempfile.TemporaryDirectory(prefix="puw-v54-s08-") as directory:
        checkpoint("s08_fixture_setup")
        fixture = integrated.__wrapped__(Path(directory))
        state = next(fixture)
        sessions, component, runtime, _identity = state
        engine = sessions.kw["bind"]
        output, stop = ctx.Queue(), ctx.Event()
        try:
            envelope = prepare(state)
            job_id = runtime.enqueue_action(envelope.action_ref.id.value, uid(999))
            args = (os.environ["PUW_V54_INTEGRATION_DATABASE_URL"], engine.v54_test_schema,
                    spawn_policy(component.policy), output, stop, "after_commit")
            worker = ctx.Process(target=t2_contender, args=args)
            worker.start()
            children.append(worker)
            checkpoint("s08_t2_committed")
            committed = output.get(timeout=25)
            assert committed["state"] == "business_committed" and committed["job_id"] == job_id
            terminate_at_boundary(worker)
            checkpoint("s08_worker_terminated")
            before_reclaim = counts(sessions, job_id)
            assert all(before_reclaim[name] == 1 for name in (
                "tasks", "history", "receipts", "projections", "success_audits"))
            assert before_reclaim["job_status"] == "running"
            expire_and_recover(sessions, job_id)
            completed = complete_with_second_worker(ctx, state, output, stop, children, job_id)
            result = counts(sessions, job_id)
            assert all(result[name] == 1 for name in (
                "tasks", "history", "receipts", "projections", "success_audits"))
            assert result["job_status"] == "completed"
            print(json.dumps({"probe": "s08_receipt_replay", "case": "S08", "status": "PASS",
                "process_kill": True, "job_before_reclaim": before_reclaim["job_status"],
                "receipt_id": completed["receipt_id"], **result}))
        finally:
            cleanup_probe(children, fixture)
    print(json.dumps({"cleanup": "test_schema_dropped"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__,
                          "checkpoint": CHECKPOINT}))
        raise SystemExit(1) from None
