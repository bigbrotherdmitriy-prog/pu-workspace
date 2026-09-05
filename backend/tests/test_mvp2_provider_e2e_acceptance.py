from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.auth import require_user
from app.database import get_db
from app.jobs.handlers import run
from app.jobs.queue import claim, execution_owner, succeed
from app.main import app
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.models.v54_provider_action import ProviderAction, ProviderOutcomeObservation
from app.provider_actions.product import RECONCILE_KIND, build_product_runtime, queue_confirmed_action


SENSITIVE_BODY = "synthetic confidential body marker"
SENSITIVE_ADDRESS = "recipient@example.test"
SCOPES = "https://www.googleapis.com/auth/gmail.send"


class _Request:
    def __init__(self, result=None, error=None):
        self.result = result or {}
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class _OfflineGmail:
    """Deterministic provider double; it never performs network I/O."""

    def __init__(self):
        self.effects = 0
        self.lookups = 0
        self.sent: list[dict] = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, **kwargs):
        self.effects += 1
        item = {"id": "offline-gmail-receipt-1", "body": kwargs["body"]}
        self.sent.append(item)
        return _Request(item, RuntimeError("synthetic timeout after effect"))

    def list(self, **_kwargs):
        self.lookups += 1
        return _Request({"messages": [{"id": item["id"]} for item in self.sent]})


def _run_claimed_job(sessions, worker_id: str) -> tuple[int, dict]:
    with sessions() as db:
        job = claim(db, worker_id)
        assert job is not None
        payload = dict(job.payload)
        locked_at = job.locked_at
        with execution_owner(job.id, worker_id, attempt=job.attempts, locked_at=locked_at):
            result = run(job.kind, payload)
        assert succeed(db, job.id, worker_id, result)
        return job.id, result


def test_confirmed_action_unknown_reconcile_receipt_is_safe_end_to_end(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.database import Base

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db:
        user = User(name="Synthetic manager", email="manager@example.test", is_admin=False)
        organization = Organization(name="Synthetic organization")
        db.add_all([user, organization])
        db.flush()
        project = Project(name="Synthetic project", organization_id=organization.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        db.add(GoogleOAuthToken(project_id=project.id, access_token=None, refresh_token=None, scopes=SCOPES))
        draft = ResponseDraft(
            project_id=project.id,
            reviewer_user_id=user.id,
            subject="Synthetic subject",
            body=SENSITIVE_BODY,
            recipient_to=SENSITIVE_ADDRESS,
            status="approved",
            source_file_id="synthetic-source",
            source_file_name="synthetic-source.txt",
            source_excerpt="synthetic evidence",
            source_excerpt_hash="a" * 64,
            confidence=1,
        )
        db.add(draft)
        db.flush()
        user_id, project_id, organization_id, draft_id = user.id, project.id, organization.id, draft.id

    with sessions() as db:
        queued = queue_confirmed_action(
            db,
            action_kind="gmail.message.send",
            target_id=draft_id,
            actor=db.get(User, user_id),
        )

    provider = _OfflineGmail()
    runtime = build_product_runtime(sessions=sessions, service_factory=lambda *_args: provider)
    monkeypatch.setattr("app.provider_actions.product.build_product_runtime", lambda: runtime)

    dispatch_job_id, dispatch_result = _run_claimed_job(sessions, "offline-dispatch-worker")
    assert dispatch_job_id == queued["job_id"]
    assert dispatch_result["outcome"] == "UNKNOWN"
    assert provider.effects == 1

    def session_override():
        with sessions() as db:
            yield db

    def user_override():
        with sessions() as db:
            return db.get(User, user_id)

    app.dependency_overrides[get_db] = session_override
    app.dependency_overrides[require_user] = user_override
    client = TestClient(app)
    try:
        unknown = client.get("/provider-actions", params={"project_id": project_id})
        assert unknown.status_code == 200
        unknown_item = unknown.json()["items"][0]
        assert unknown_item["business_status"] == "requires_reconciliation"
        assert unknown_item["reconciliation_status"] == "required"
        assert unknown_item["receipt_outcome"] == "UNKNOWN"

        enqueued = client.post(
            f"/provider-actions/{queued['action_id']}/revisions/{queued['revision']}/reconcile"
        )
        assert enqueued.status_code == 200
        assert enqueued.json()["action_id"] == queued["action_id"]
        replayed = client.post(
            f"/provider-actions/{queued['action_id']}/revisions/{queued['revision']}/reconcile"
        )
        assert replayed.status_code == 200
        assert replayed.json() == {**enqueued.json(), "already_queued": True}
        with sessions() as db:
            assert len(list(db.scalars(select(BackgroundJob).where(
                BackgroundJob.kind == RECONCILE_KIND
            )))) == 1

        waiting = client.get(
            f"/provider-actions/{queued['action_id']}/revisions/{queued['revision']}/status",
            params={"project_id": project_id},
        )
        assert waiting.json()["reconciliation_status"] == "queued"

        reconciliation_job_id, reconciliation_result = _run_claimed_job(
            sessions, "offline-reconcile-worker"
        )
        assert reconciliation_job_id == enqueued.json()["job_id"]
        assert reconciliation_result["outcome"] == "APPLIED"
        assert provider.effects == 1
        assert provider.lookups == 1

        resolved = client.get("/provider-actions", params={"project_id": project_id})
        assert resolved.status_code == 200
        resolved_item = resolved.json()["items"][0]
        assert resolved_item["business_status"] == "completed"
        assert resolved_item["reconciliation_status"] == "resolved"
        assert resolved_item["reconciliation"]["status"] == "completed"
        assert resolved_item["receipt_outcome"] == "APPLIED"
        assert resolved_item["receipt_late"] is True
        assert resolved_item["receipt_id"] != unknown_item["receipt_id"]

        combined = unknown.text + waiting.text + resolved.text + repr(enqueued.json())
        assert SENSITIVE_BODY not in combined
        assert SENSITIVE_ADDRESS not in combined
        for forbidden in ("payload", "mailbox_key", "external_ref", "last_error", "result"):
            assert forbidden not in combined

        with sessions() as db:
            jobs = list(db.scalars(select(BackgroundJob).order_by(BackgroundJob.id)))
            observations = list(db.scalars(select(ProviderOutcomeObservation).order_by(
                ProviderOutcomeObservation.sequence
            )))
            actions = list(db.scalars(select(ProviderAction)))
            audits = list(db.scalars(select(AuditLog)))
        assert len(actions) == 1
        assert [row.status for row in jobs] == ["completed", "completed"]
        assert [row.outcome for row in observations] == ["UNKNOWN", "APPLIED"]
        assert all(set(row.payload) == {"organization_id", "action_id", "revision"} for row in jobs)
        stored_transport_and_audit = repr([row.payload for row in jobs]) + repr([
            row.details for row in audits
        ])
        assert SENSITIVE_BODY not in stored_transport_and_audit
        assert SENSITIVE_ADDRESS not in stored_transport_and_audit
        assert organization_id == jobs[0].payload["organization_id"]
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()
