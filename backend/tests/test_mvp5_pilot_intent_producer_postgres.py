from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.models.v54_pilot import ActionRevision, PendingDispatch, PilotAction
from app.pilot_intent_producer import run_product_intent_job
from test_mvp5_pilot_intent_producer_offline import (
    NOW, producer_payload, producer_settings, seed_inbound,
)
from test_mvp5_product_pilot import product_world


def test_postgres_concurrent_producers_create_one_intent(product_world):
    sessions, _component, _runtime, settings, _view = product_world
    if sessions.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PUW_V54_INTEGRATION_DATABASE_URL is required")
    message_id = seed_inbound(sessions, 601)
    barrier = Barrier(2)

    def produce():
        barrier.wait()
        return run_product_intent_job(
            producer_payload(message_id), sessions=sessions,
            settings=producer_settings(settings), clock=lambda: NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in (pool.submit(produce), pool.submit(produce))]
    assert {item["state"] for item in outcomes} == {"pending"}
    assert len({item["action_id"] for item in outcomes}) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PilotAction)) == 1
        assert db.scalar(select(func.count()).select_from(ActionRevision)) == 1
        assert db.scalar(select(func.count()).select_from(PendingDispatch)) == 1
