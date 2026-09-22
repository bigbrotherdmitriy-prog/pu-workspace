from dataclasses import replace
from datetime import date, timedelta

from sqlalchemy import func, select

from app.models.ai_secretary import Message
from app.models.task import Task
from app.models.v54_pilot import (
    ActionRevision, PendingDispatch, PilotAction, SourceCurrent,
    SourceReference, SourceVersion,
)
from app.pilot_intent_producer import PRODUCT_INTENT_KIND, run_product_intent_job
from app.pilot_product import load_product_pilot_settings
from test_mvp5_product_pilot import NOW, ident, product_world


def seed_inbound(sessions, number: int = 501, *, owner_confirmed: bool = True,
                 with_task: bool = True):
    text = f"Дмитрий, подготовь акт {number}. Срок: 25.09.2026."
    with sessions.begin() as db:
        source = SourceReference(
            id=ident("producer-source", number), organization_id=1, origin_project_id=10,
            identity_id=ident("identity", 1), parent_source_id=None, namespace="gmail",
            external_id=f"gmail-producer-{number}", external_id_kind="provider_message_id",
            incarnation=1, object_kind="message",
            canonical_locator={"kind": "gmail_message", "provider_message_id": f"gmail-producer-{number}"},
            record_version=1, freshness="fresh", sync_state="current", availability="available",
            last_seen_at=NOW, last_checked_at=NOW, next_check_at=NOW + timedelta(hours=1),
            policy_pins={"access": "owner", "retention": "24h", "residency": "configured"},
            residency={"source_location": "google_workspace", "assurance": "owner_pilot"},
        )
        version = SourceVersion(
            id=ident("producer-version", number), organization_id=1, source_id=source.id,
            revision=1, observation_key=f"history-{number}", provider_revision=f"r-{number}",
            consistency="revision_bound", locator_at_observation=dict(source.canonical_locator),
            integrity=[], observed_at=NOW,
        )
        db.add_all([source, version]); db.flush()
        db.add(SourceCurrent(source_id=source.id, organization_id=1, version_id=version.id))
        message = Message(
            organization_id=1, project_id=10, contract_id=20, created_by_user_id=2,
            source_type="email", source_external_id=source.external_id,
            source_name="Inbound producer fixture", content=text, attachments_json="[]",
            summary="", context_confidence=0.95, context_evidence="model candidate",
            context_confirmed=True, analysis_required=not with_task, status="ready",
            mail_connection_id=ident("mail", 1), provider_message_id=source.external_id,
            source_reference_id=source.id, context_version=2, origin_version=1,
            context_confirmed_by_user_id=2 if owner_confirmed else None,
            context_confirmed_by_user_at=NOW if owner_confirmed else None,
            context_confirmed_context_version=2 if owner_confirmed else None,
            context_confirmed_authority_epoch=1 if owner_confirmed else None,
        )
        db.add(message); db.flush()
        if with_task:
            db.add(Task(
                project_id=10, message_id=message.id, assignee_user_id=2, created_by_user_id=2,
                title=f"Дмитрий, подготовь акт {number}", due_date=date(2026, 9, 25),
                status="assigned", source_type="email", source_file_id=f"message:{message.id}",
                source_file_name=message.source_name, source_excerpt=text,
                source_excerpt_hash=f"{number:064x}"[-64:], confidence=0.95,
                needs_review=True, external_action_status="proposed", extraction_method="regex",
            ))
        db.flush()
        return message.id


def producer_settings(settings, *, enabled=True):
    return replace(settings, producer_enabled=enabled)


def producer_payload(message_id: int):
    return {"message_id": message_id, "expected_context_version": 2,
            "owner_user_id": 2, "project_id": 10}


def test_producer_is_default_off_and_does_not_create_an_intent(product_world, monkeypatch):
    sessions, _component, _runtime, settings, _view = product_world
    monkeypatch.delenv("PU_V54_AUTO_INTENT_PRODUCER_ENABLED", raising=False)
    monkeypatch.setenv("PU_V54_AUTO_PILOT_ENABLED", "true")
    monkeypatch.setenv("PU_V54_AUTO_PILOT_PROJECT_ID", "10")
    monkeypatch.setenv("PU_V54_AUTO_PILOT_OWNER_USER_ID", "2")
    assert load_product_pilot_settings().producer_enabled is False
    message_id = seed_inbound(sessions)
    result = run_product_intent_job(
        producer_payload(message_id), sessions=sessions, settings=settings, clock=lambda: NOW,
    )
    assert result == {"state": "confirm", "reason": "producer_disabled", "action_id": None}
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PilotAction)) == 0


def test_durable_handler_routes_product_intent_job(monkeypatch):
    from app.jobs import handlers
    payload = producer_payload(999)
    monkeypatch.setattr(
        "app.pilot_intent_producer.run_product_intent_job",
        lambda value: {"state": "confirm", "reason": "test", "payload": value},
    )
    assert handlers.run(PRODUCT_INTENT_KIND, payload) == {
        "state": "confirm", "reason": "test", "payload": payload,
    }


def test_owner_confirmation_and_confidence_create_one_durable_intent(product_world):
    sessions, _component, _runtime, settings, _view = product_world
    message_id = seed_inbound(sessions)
    result = run_product_intent_job(
        producer_payload(message_id), sessions=sessions,
        settings=producer_settings(settings), clock=lambda: NOW,
    )
    assert result["state"] == "pending" and result["action_id"]
    with sessions() as db:
        action = db.get(PilotAction, result["action_id"])
        pending = db.get(PendingDispatch, result["action_id"])
        revision = db.get(ActionRevision, (result["action_id"], 1))
        assert action.message_id == message_id
        assert action.action_type == "task.internal.create"
        assert action.business_state == "READY"
        assert pending.pending is True and pending.authorization_origin == "SERVER_POLICY"
        assert revision.envelope["autonomy"] == "AUTO"
        assert revision.envelope["payload"]["publish_external"] is False
        assert revision.envelope["payload"]["create_obligation"] is False


def test_high_confidence_without_owner_confirmation_stays_confirm(product_world):
    sessions, _component, _runtime, settings, _view = product_world
    message_id = seed_inbound(sessions, 502, owner_confirmed=False)
    result = run_product_intent_job(
        producer_payload(message_id), sessions=sessions,
        settings=producer_settings(settings), clock=lambda: NOW,
    )
    assert result["state"] == "confirm"
    assert result["reason"] == "producer_gate_not_applicable"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PilotAction)) == 0


def test_owner_confirmation_without_required_confidence_stays_confirm(product_world):
    sessions, _component, _runtime, settings, _view = product_world
    message_id = seed_inbound(sessions, 503)
    with sessions.begin() as db:
        message = db.get(Message, message_id)
        message.context_confidence = 0.89
    result = run_product_intent_job(
        producer_payload(message_id), sessions=sessions,
        settings=producer_settings(settings), clock=lambda: NOW,
    )
    assert result["state"] == "confirm"
    assert result["reason"] == "producer_gate_not_applicable"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PilotAction)) == 0
