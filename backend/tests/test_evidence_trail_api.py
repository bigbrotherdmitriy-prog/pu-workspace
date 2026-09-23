"""The unified trail is a read-only, project-scoped projection, not a new journal."""
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.auth import require_user
from app.core.v54_dto import canonical_hash
from app.database import Base, get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_pilot import ActionReceipt, ActionRevision, AuditExtension, PilotAction
from app.models.v54_provider_action import ProviderAction, ProviderOutcomeObservation
from v54_pilot_fixture import NOW, seed, uid


@pytest.fixture
def world(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'trail.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as db:
        fixture = seed(db)
        db.add_all([
            ProjectMember(project_id=4, user_id=2, role="owner"),
            ProjectMember(project_id=4, user_id=3, role="manager"),
            User(id=7, name="Viewer", email="viewer-trail@example.test", is_admin=False),
            User(id=8, name="Admin without membership", email="admin-trail@example.test", is_admin=True),
        ])
        db.flush()
        db.add(ProjectMember(project_id=4, user_id=7, role="viewer"))

        # A linked audit event and an unlinked historical event.  The sensitive
        # free-form details must never cross this endpoint.
        linked = AuditLog(action="v54.action.frozen", entity_type="message", entity_id=6,
                          details="provider_payload=DO-NOT-RETURN", created_at=NOW)
        legacy = AuditLog(action="message_processed", entity_type="message", entity_id=6,
                          details="source_text=TOP-SECRET-BODY", created_at=NOW + timedelta(minutes=1))
        other = AuditLog(action="project_archived", entity_type="project", entity_id=9,
                         details="OTHER-PROJECT-SECRET", created_at=NOW + timedelta(minutes=2))
        db.add_all([linked, legacy, other])
        db.flush()
        db.add(AuditExtension(
            organization_id=1, audit_log_id=linked.id, subject_type="action", subject_id=uid(20), sequence=1,
            actor_id=2, service_principal=None, project_id=4, action_pin=None, subject_pin=None,
            approval_id=None, receipt_id=None, job_id=None, relation_refs=[], correlation_id="trail-test",
        ))

        # A later, valid internal-create revision makes the AUTO and CONFIRM
        # paths visibly distinct without inventing an external effect.
        auto_envelope = deepcopy(fixture["envelope"])
        auto_envelope["revision"] = 2
        auto_envelope["autonomy"] = "AUTO"
        auto_envelope["idempotency_key"] = "synthetic-auto-trail"
        db.add(ActionRevision(
            action_id=uid(20), revision=2, organization_id=1, claim_id=uid(17), claim_revision=1,
            policy_id=uid(22), policy_revision=1, envelope=auto_envelope,
            envelope_hash=canonical_hash(auto_envelope), command_key=auto_envelope["idempotency_key"],
            requested_by=2, created_at=NOW + timedelta(minutes=3),
        ))

        # Existing CONFIRM revision receives a real human-authorized receipt.
        job = BackgroundJob(kind="synthetic.trail", payload={}, status="completed")
        db.add(job)
        db.flush()
        db.add(ActionReceipt(
            id=uid(51), organization_id=1, action_id=uid(20), revision=1,
            envelope_hash=canonical_hash(fixture["envelope"]), authorization_origin="HUMAN_APPROVAL",
            approval_id=uid(23), policy_id=None, policy_revision=None, policy_hash=None,
            authority_epoch=None, decision_hash=None, action_hash=None, payload_hash=None,
            authorization_decision=None, authorization_valid_until=None,
            job_id=job.id, fence=1, outcome="APPLIED", target_ref=None,
            recorded_at=NOW + timedelta(minutes=4),
        ))

        provider = ProviderAction(
            action_id="synthetic-provider-trail", revision=1, organization_id=1, project_id=4,
            mailbox_key="a" * 64, provider="synthetic", mode="CONFIRM", synthetic_only=True,
            action_kind="synthetic.create", reversibility="COMPENSATABLE", payload_hash="b" * 64,
            command_key="provider-trail-command", idempotency_key="provider-trail-idempotency",
            context_revision=1, evidence_pins=[], authority_epoch=1, capability_version=1,
            credential_generation=1, relation_kind=None, relation_action_id=None,
            envelope_hash="c" * 64, state="APPLIED", created_by="synthetic-test", created_at=NOW,
        )
        db.add(provider)
        db.flush()
        db.add(ProviderOutcomeObservation(
            action_id=provider.action_id, revision=1, organization_id=1, sequence=1,
            attempt_id="trail-attempt", job_id=None, mailbox_key=provider.mailbox_key,
            command_key=provider.command_key, idempotency_key=provider.idempotency_key,
            payload_hash=provider.payload_hash, envelope_hash=provider.envelope_hash,
            outcome="APPLIED", retry_safe=False, source="DISPATCH", late=False,
            external_ref="opaque-do-not-return", safe_code="ok", recorded_at=NOW + timedelta(minutes=5),
        ))

    current_user = {"id": 2}

    def override_db():
        with sessions() as db:
            yield db

    def override_user():
        with sessions() as db:
            return db.get(User, current_user["id"])

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_user] = override_user
    try:
        yield sessions, current_user, TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_project_trail_projects_typed_auto_confirm_provider_and_legacy_without_bodies(world):
    _sessions, _current_user, client = world
    response = client.get("/api/v54/projects/4/evidence-trail?limit=100")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["project_id"] == 4
    assert {item["authorization"] for item in body["items"]} >= {"AUTO", "CONFIRM", "UNKNOWN"}
    assert any(item["event"] == "SOURCE_OBSERVED" for item in body["items"])
    assert any(item["event"] == "EVIDENCE_EXTRACTED" and item["evidence_refs"] for item in body["items"])
    assert any(item["event"] == "DEADLINE_CONFIRMED" and item["authorization"] == "CONFIRM" for item in body["items"])
    assert any(item["id"].startswith("receipt:") and item["outcome"] == "APPLIED" for item in body["items"])
    assert any(item["id"].startswith("provider-observation:") and item["outcome"] == "APPLIED" for item in body["items"])
    legacy = next(item for item in body["items"] if item["event"] == "message_processed")
    assert legacy["linkage"] == "legacy_unlinked"
    assert legacy["authorization"] == "UNKNOWN"
    serialized = response.text
    for forbidden in ("TOP-SECRET-BODY", "DO-NOT-RETURN", "OTHER-PROJECT-SECRET", "Synthetic only", "opaque-do-not-return"):
        assert forbidden not in serialized


def test_trail_requires_explicit_owner_or_manager_membership_and_uses_uniform_404(world):
    _sessions, current_user, client = world
    current_user["id"] = 3
    assert client.get("/api/v54/projects/4/evidence-trail").status_code == 200
    for user_id, project_id in ((7, 4), (8, 4), (2, 9), (2, 99999)):
        current_user["id"] = user_id
        response = client.get(f"/api/v54/projects/{project_id}/evidence-trail")
        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found"}


def test_trail_cursor_is_stable_opaque_and_invalid_cursor_is_rejected(world):
    _sessions, _current_user, client = world
    first = client.get("/api/v54/projects/4/evidence-trail?limit=2")
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor
    second = client.get("/api/v54/projects/4/evidence-trail", params={"limit": 100, "cursor": cursor})
    assert second.status_code == 200
    assert set(item["id"] for item in first.json()["items"]).isdisjoint(item["id"] for item in second.json()["items"])
    assert client.get("/api/v54/projects/4/evidence-trail?cursor=not-a-cursor").status_code == 422


def test_trail_get_performs_no_mutation(world):
    sessions, _current_user, client = world
    tracked = (AuditLog, AuditExtension, PilotAction, ActionRevision, ActionReceipt, ProviderOutcomeObservation, BackgroundJob)
    with sessions() as db:
        before = {model: db.scalar(select(func.count()).select_from(model)) for model in tracked}
    assert client.get("/api/v54/projects/4/evidence-trail?limit=100").status_code == 200
    with sessions() as db:
        after = {model: db.scalar(select(func.count()).select_from(model)) for model in tracked}
    assert after == before


def test_trail_bulk_loads_typed_relations_without_per_action_n_plus_one(world):
    sessions, _current_user, client = world
    statements = 0

    def count_statement(*_args):
        nonlocal statements
        statements += 1

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        assert client.get("/api/v54/projects/4/evidence-trail?limit=100").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)
    # The projection spans many legacy domain tables, but approvals, receipts,
    # revisions and provider observations are loaded in batches rather than per
    # action. Keep an explicit ceiling so an accidental N+1 cannot return.
    assert statements < 60
