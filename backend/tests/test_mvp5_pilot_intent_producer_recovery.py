from sqlalchemy import func, select

from app.models.job import BackgroundJob
from app.models.v54_pilot import ActionRevision, PendingDispatch, PilotAction
from app.pilot_intent_producer import run_product_intent_job
from test_mvp5_pilot_intent_producer_offline import (
    NOW, producer_payload, producer_settings, seed_inbound,
)
from test_mvp5_product_pilot import product_world


def test_retry_after_commit_returns_same_intent_and_recovery_enqueues_once(product_world):
    sessions, _component, runtime, settings, _view = product_world
    message_id = seed_inbound(sessions, 701)
    arguments = dict(sessions=sessions, settings=producer_settings(settings), clock=lambda: NOW)
    first = run_product_intent_job(producer_payload(message_id), **arguments)
    # Simulate a worker crash after the producer transaction committed but before
    # its BackgroundJob was marked completed: the same handler runs again.
    second = run_product_intent_job(producer_payload(message_id), **arguments)
    assert first == second
    assert runtime.recover() == 1
    assert runtime.recover() == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PilotAction)) == 1
        assert db.scalar(select(func.count()).select_from(ActionRevision)) == 1
        pending = db.get(PendingDispatch, first["action_id"])
        assert pending.pending is True and pending.job_id is not None
        assert db.scalar(select(func.count()).select_from(BackgroundJob).where(
            BackgroundJob.kind == "v54.product_task",
        )) == 1
