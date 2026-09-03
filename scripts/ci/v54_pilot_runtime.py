"""Opt-in PostgreSQL process-fault probe. Never falls back to SQLite.

Run from repo root with backend dependencies and an explicitly isolated test DB:
  PUW_V54_INTEGRATION_DATABASE_URL=... python scripts/ci/v54_pilot_runtime.py
Outputs IDs/counts only. No DSN, exception text, envelopes or document data.
This does NOT prove production authority, migration correctness or external once.
"""
import multiprocessing as mp
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]


def contender(url, schema, policy, output, stop, pause_after_claim):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.jobs.queue import claim, execution_owner, succeed
    from app.jobs.handlers import run
    from app.pilot_composition import SyntheticComposition
    from app.pilot_dispatch import SyntheticDispatch, install_synthetic_runtime
    from v54_pilot_fixture import NOW
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5,
        "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000"})
    sessions = sessionmaker(engine, expire_on_commit=False)
    component = SyntheticComposition(policy=policy, clock=lambda: NOW, enabled=True)
    install_synthetic_runtime(SyntheticDispatch(sessions=sessions, composition_for_scope=lambda s: component))
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


def main():
    assert os.getenv("PUW_V54_INTEGRATION_DATABASE_URL"), "Explicit isolated PostgreSQL URL required"
    from sqlalchemy import select, func
    from datetime import timedelta
    from test_v54_pilot_integration import integrated, prepare
    from app.jobs.queue import recover_expired
    from app.models.job import BackgroundJob
    from app.models.task import Task
    from app.models.v54_pilot import ActionReceipt
    from v54_pilot_fixture import NOW, uid
    ctx = mp.get_context("spawn")
    children = []
    with tempfile.TemporaryDirectory(prefix="puw-v54-pg-") as directory:
        fixture = integrated.__wrapped__(Path(directory))
        state = next(fixture)
        sessions, component, runtime, _ = state
        engine = sessions.kw["bind"]
        assert engine.dialect.name == "postgresql"
        try:
            envelope = prepare(state)
            job_id = runtime.enqueue_action(envelope.action_ref.id.value, uid(999))
            output, stop = ctx.Queue(), ctx.Event()
            args = (os.environ["PUW_V54_INTEGRATION_DATABASE_URL"], engine.v54_test_schema, component.policy, output, stop)
            first = ctx.Process(target=contender, args=(*args, True))
            children.append(first)
            first.start()
            claimed = output.get(timeout=25)
            assert claimed["state"] == "claimed"
            rival = ctx.Process(target=contender, args=(*args, False))
            children.append(rival)
            rival.start()
            assert output.get(timeout=25)["state"] == "no_claim"
            rival.join(10)
            assert rival.exitcode == 0
            # Kill only the test process created immediately above; not Docker or prod.
            first.terminate()
            first.join(10)
            assert not first.is_alive()
            with sessions.begin() as db:
                # Accelerated lease expiry, explicitly NOT a wall-clock expiry proof.
                db.get(BackgroundJob, job_id).lease_expires_at = NOW - timedelta(seconds=1)
            with sessions() as db:
                assert recover_expired(db) == 1
            try:
                runtime.execute(claimed["payload"], claimed["owner"])
            except ValueError:
                pass
            else:
                raise AssertionError("stale worker accepted before recovery")
            second = ctx.Process(target=contender, args=(*args, False))
            children.append(second)
            second.start()
            assert output.get(timeout=25)["state"] == "claimed"
            completed = output.get(timeout=25)
            assert completed["state"] == "completed"
            second.join(10)
            assert second.exitcode == 0
            with sessions() as db:
                assert db.scalar(select(func.count()).select_from(Task)) == 1
                assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 1
                assert db.get(BackgroundJob, job_id).status == "completed"
            print(json.dumps({"probe": "process_reclaim", "job_id": job_id, "receipt_id": completed["receipt_id"],
                   "status": "PASS", "expiry": "accelerated", "external_effects": "not_tested"}))
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                child.join(10)
            fixture.close()
    print(json.dumps({"cleanup": "test_schema_dropped"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
