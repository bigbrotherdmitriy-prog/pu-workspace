from sqlalchemy import select

from app.api import ai_secretary as ai
from app.models.job import BackgroundJob
from app.models.task import Task
from app.models.user import User
from app.models.v54_pilot import PilotAction
from app.pilot_intent_producer import PRODUCT_INTENT_KIND, run_product_intent_job
from test_mvp5_pilot_intent_producer_offline import (
    NOW, producer_settings, seed_inbound,
)
from test_mvp5_product_pilot import product_world


def test_gmail_materialization_owner_confirmation_enqueues_and_produces(
        product_world, monkeypatch):
    sessions, _component, _runtime, settings, _view = product_world
    message_id = seed_inbound(sessions, 801, owner_confirmed=False, with_task=False)
    monkeypatch.setenv("PU_V54_AUTO_PILOT_ENABLED", "true")
    monkeypatch.setenv("PU_V54_AUTO_INTENT_PRODUCER_ENABLED", "true")
    monkeypatch.setenv("PU_V54_AUTO_PILOT_PROJECT_ID", "10")
    monkeypatch.setenv("PU_V54_AUTO_PILOT_OWNER_USER_ID", "2")
    with sessions() as db:
        message = db.get(ai.Message, message_id)
        tasks, _drafts, _risks, _suggestions = ai._analyze_confirmed_message(
            db, message, response_suppressed=True,
        )
        db.commit()
        assert len(tasks) == 1
        owner = db.get(User, 2)
        response = ai.confirm_context_for_auto(
            message_id,
            ai.AutoContextConfirmation(
                project_id=10, contract_id=20, expected_context_version=2,
            ),
            db,
            owner,
        )
        assert response["producer_job_id"] is not None
        job = db.get(BackgroundJob, response["producer_job_id"])
        assert job.kind == PRODUCT_INTENT_KIND
        payload = dict(job.payload)
    outcome = run_product_intent_job(
        payload, sessions=sessions, settings=producer_settings(settings), clock=lambda: NOW,
    )
    assert outcome["state"] == "pending"
    with sessions() as db:
        assert db.scalar(select(PilotAction).where(PilotAction.message_id == message_id)) is not None
        task = db.scalar(select(Task).where(Task.message_id == message_id))
        assert task.needs_review is True and task.external_action_status == "proposed"


def test_invalid_product_pilot_configuration_fails_closed_after_confirmation(
        product_world, monkeypatch):
    sessions, _component, _runtime, _settings, _view = product_world
    message_id = seed_inbound(sessions, 802, owner_confirmed=False)
    monkeypatch.setenv("PU_V54_AUTO_PILOT_ENABLED", "true")
    monkeypatch.setenv("PU_V54_AUTO_INTENT_PRODUCER_ENABLED", "true")
    monkeypatch.delenv("PU_V54_AUTO_PILOT_PROJECT_ID", raising=False)
    monkeypatch.setenv("PU_V54_AUTO_PILOT_OWNER_USER_ID", "2")
    with sessions() as db:
        owner = db.get(User, 2)
        response = ai.confirm_context_for_auto(
            message_id,
            ai.AutoContextConfirmation(
                project_id=10, contract_id=20, expected_context_version=2,
            ),
            db,
            owner,
        )
        assert response["state"] == "confirmed_current"
        assert response["producer_job_id"] is None
        assert db.scalar(select(BackgroundJob).where(
            BackgroundJob.kind == PRODUCT_INTENT_KIND,
        )) is None
