from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register all mapped tables
from app.core.auth import require_user
from app.database import Base, get_db
from app.main import app
from app.models.job import BackgroundJob
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_provider_action import (
    ProviderAction,
    ProviderActionApproval,
    ProviderDispatchOutbox,
    ProviderOutcomeObservation,
)
from app.provider_actions.product import RECONCILE_KIND


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
SENSITIVE = "recipient@example.test secret-provider-response bearer-token-value"


def _action_values(*, project: Project, action_id: str, revision: int, state: str) -> dict:
    marker = str(revision)
    return {
        "action_id": action_id,
        "revision": revision,
        "organization_id": project.organization_id,
        "project_id": project.id,
        "mailbox_key": (f"{project.id:x}{revision:x}" * 64)[:64],
        "provider": "google_workspace",
        "mode": "CONFIRM",
        "synthetic_only": False,
        "action_kind": "google.tasks.upsert",
        "reversibility": "REVERSIBLE",
        "payload_hash": "a" * 64,
        "command_key": f"command:{action_id}:{SENSITIVE}:{revision}",
        "idempotency_key": f"idempotency:{action_id}:{SENSITIVE}:{revision}",
        "context_revision": 1,
        "evidence_pins": [f"evidence:{SENSITIVE}"],
        "authority_epoch": 1,
        "capability_version": 1,
        "credential_generation": 1,
        "relation_kind": None,
        "relation_action_id": None,
        "envelope_hash": "b" * 64,
        "state": state,
        "created_by": "1",
        "created_at": NOW + timedelta(minutes=revision),
    }


def _seed_action(
    db,
    *,
    project: Project,
    action_id: str,
    revision: int = 1,
    state: str = "READY",
    job_status: str = "queued",
    observation: str | None = None,
    safe_code: str | None = None,
    approval_state: str = "GRANTED",
    approval_expires_at: datetime | None = None,
    reconciliation_status: str | None = None,
):
    values = _action_values(
        project=project,
        action_id=action_id,
        revision=revision,
        state=state,
    )
    action = ProviderAction(**values)
    approval = ProviderActionApproval(
        id=f"approval-{project.id}-{action_id}-{revision}",
        action_id=action_id,
        revision=revision,
        organization_id=project.organization_id,
        project_id=project.id,
        mailbox_key=values["mailbox_key"],
        command_key=values["command_key"],
        idempotency_key=values["idempotency_key"],
        payload_hash=values["payload_hash"],
        envelope_hash=values["envelope_hash"],
        authority_epoch=1,
        capability_version=1,
        credential_generation=1,
        state=approval_state,
        approved_by="1",
        granted_at=NOW,
        expires_at=approval_expires_at or NOW + timedelta(days=3650),
    )
    job = BackgroundJob(
        kind="provider.action.execute",
        payload={
            "organization_id": project.organization_id,
            "action_id": action_id,
            "revision": revision,
            "unsafe_test_value": SENSITIVE,
        },
        status=job_status,
        progress=37,
        attempts=2,
        max_attempts=3,
        last_error=SENSITIVE,
        result={"provider_response": SENSITIVE},
        idempotency_key=f"dispatch-job-{project.id}-{action_id}-{revision}",
    )
    db.add_all([action, approval, job])
    db.flush()
    db.add(ProviderDispatchOutbox(
        action_id=action_id,
        revision=revision,
        organization_id=project.organization_id,
        approval_id=approval.id,
        envelope_hash=values["envelope_hash"],
        pending=job_status not in {"completed", "cancelled"},
        job_id=job.id,
        created_at=NOW,
    ))
    db.flush()

    observed = None
    if observation is not None:
        observed = ProviderOutcomeObservation(
            action_id=action_id,
            revision=revision,
            organization_id=project.organization_id,
            sequence=1,
            attempt_id=f"attempt-{project.id}-{action_id}-{revision}",
            job_id=job.id,
            mailbox_key=values["mailbox_key"],
            command_key=values["command_key"],
            idempotency_key=values["idempotency_key"],
            payload_hash=values["payload_hash"],
            envelope_hash=values["envelope_hash"],
            outcome=observation,
            retry_safe=False,
            source="DISPATCH",
            late=False,
            external_ref=SENSITIVE,
            safe_code=safe_code,
            recorded_at=NOW,
        )
        db.add(observed)
        db.flush()

    reconcile_job = None
    if reconciliation_status is not None:
        assert observed is not None and observation == "UNKNOWN"
        reconcile_job = BackgroundJob(
            kind=RECONCILE_KIND,
            payload={
                "organization_id": project.organization_id,
                "action_id": action_id,
                "revision": revision,
            },
            status=reconciliation_status,
            progress=50,
            attempts=1,
            max_attempts=3,
            last_error=SENSITIVE,
            idempotency_key=(
                f"provider-reconcile:{project.organization_id}:"
                f"{action_id}:{revision}:1"
            ),
        )
        db.add(reconcile_job)
        db.flush()
    return SimpleNamespace(action=action, approval=approval, job=job, observation=observed,
                           reconciliation=reconcile_job)


@pytest.fixture
def controls_api(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'provider-controls.db'}")
    sessions = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with sessions.begin() as db:
        first_org = Organization(name="First synthetic organization")
        second_org = Organization(name="Second synthetic organization")
        db.add_all([first_org, second_org])
        db.flush()
        first_project = Project(name="First project", organization_id=first_org.id)
        second_project = Project(name="Second project", organization_id=second_org.id)
        db.add_all([first_project, second_project])
        db.flush()
        owner = User(name="Owner", email="owner@example.test", is_admin=False)
        outsider = User(name="Outsider", email="outsider@example.test", is_admin=False)
        db.add_all([owner, outsider])
        db.flush()
        db.add_all([
            ProjectMember(project_id=first_project.id, user_id=owner.id, role="viewer"),
            ProjectMember(project_id=second_project.id, user_id=outsider.id, role="viewer"),
        ])
        db.flush()
        _seed_action(
            db,
            project=first_project,
            action_id="google-task-101",
            revision=1,
            state="UNKNOWN",
            job_status="completed",
            observation="UNKNOWN",
            safe_code="timeout_after_effect",
            reconciliation_status="retrying",
        )
        _seed_action(
            db,
            project=first_project,
            action_id="google-task-revised",
            revision=1,
            state="NOT_APPLIED",
            job_status="completed",
            observation="NOT_APPLIED",
        )
        _seed_action(
            db,
            project=first_project,
            action_id="google-task-revised",
            revision=2,
            state="READY",
            job_status="queued",
        )
        _seed_action(
            db,
            project=first_project,
            action_id="google-task-unsafe-reason",
            state="UNKNOWN",
            job_status="completed",
            observation="UNKNOWN",
            safe_code=SENSITIVE,
        )
        _seed_action(
            db,
            project=second_project,
            action_id="google-task-foreign",
            state="APPLIED",
            job_status="completed",
            observation="APPLIED",
        )
        db.commit()

    current_user_id = {"value": owner.id}

    def session_override():
        with sessions() as db:
            yield db

    def user_override():
        with sessions() as db:
            return db.get(User, current_user_id["value"])

    app.dependency_overrides[get_db] = session_override
    app.dependency_overrides[require_user] = user_override
    try:
        yield SimpleNamespace(
            client=TestClient(app),
            sessions=sessions,
            user_id=current_user_id,
            owner_id=owner.id,
            outsider_id=outsider.id,
            first_project_id=first_project.id,
            second_project_id=second_project.id,
        )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_project_list_is_allowlisted_and_redacts_provider_material(controls_api):
    response = controls_api.client.get(
        "/provider-actions",
        params={"project_id": controls_api.first_project_id},
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["count"] == 4
    assert {item["action_id"] for item in body["items"]} == {
        "google-task-101", "google-task-revised", "google-task-unsafe-reason",
    }
    assert SENSITIVE not in response.text
    forbidden = {
        "payload", "payload_hash", "mailbox_key", "command_key", "idempotency_key",
        "external_ref", "last_error", "result", "evidence_pins", "approved_by",
    }
    for item in body["items"]:
        assert forbidden.isdisjoint(item)
        assert forbidden.isdisjoint(item.get("dispatch") or {})
        assert forbidden.isdisjoint(item.get("reconciliation") or {})


def test_non_allowlisted_database_reason_is_replaced_with_generic_code(controls_api):
    response = controls_api.client.get(
        "/provider-actions/google-task-unsafe-reason/revisions/1/status",
        params={"project_id": controls_api.first_project_id},
    )

    assert response.status_code == 200
    assert response.json()["safe_reason"] == "outcome_unknown"
    assert response.json()["reconciliation_status"] == "required"
    assert SENSITIVE not in response.text


def test_invalid_cross_job_binding_is_not_exposed(controls_api):
    with controls_api.sessions.begin() as db:
        unrelated = BackgroundJob(
            kind="folder.analyze",
            payload={"content": SENSITIVE},
            status="failed",
            progress=99,
            attempts=3,
            max_attempts=3,
            last_error=SENSITIVE,
            idempotency_key="unrelated-sensitive-job",
        )
        db.add(unrelated)
        db.flush()
        outbox = db.get(ProviderDispatchOutbox, ("google-task-revised", 2))
        outbox.job_id = unrelated.id

    response = controls_api.client.get(
        "/provider-actions/google-task-revised/revisions/2/status",
        params={"project_id": controls_api.first_project_id},
    )

    assert response.status_code == 200
    assert response.json()["dispatch"] is None
    assert SENSITIVE not in response.text


def test_unknown_action_exposes_retry_and_reconciliation_without_raw_error(controls_api):
    response = controls_api.client.get(
        "/provider-actions/google-task-101/revisions/1/status",
        params={"project_id": controls_api.first_project_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["business_status"] == "requires_reconciliation"
    assert body["reconciliation_status"] == "retrying"
    assert body["retry_state"] == "retrying"
    assert body["safe_reason"] == "timeout_after_effect"
    assert body["receipt_id"] > 0
    assert body["receipt_outcome"] == "UNKNOWN"
    assert body["reconciliation"]["status"] == "retrying"
    assert SENSITIVE not in response.text


def test_exact_revision_never_falls_forward_to_latest(controls_api):
    historical = controls_api.client.get(
        "/provider-actions/google-task-revised/revisions/1",
        params={"project_id": controls_api.first_project_id},
    ).json()
    current = controls_api.client.get(
        "/provider-actions/google-task-revised/revisions/2",
        params={"project_id": controls_api.first_project_id},
    ).json()

    assert historical["revision"] == 1
    assert historical["business_status"] == "not_applied"
    assert historical["is_current_revision"] is False
    assert current["revision"] == 2
    assert current["business_status"] == "queued"
    assert current["is_current_revision"] is True


def test_unknown_action_and_revision_are_generic_not_found(controls_api):
    missing_action = controls_api.client.get(
        "/provider-actions/missing/revisions/1",
        params={"project_id": controls_api.first_project_id},
    )
    missing_revision = controls_api.client.get(
        "/provider-actions/google-task-101/revisions/999",
        params={"project_id": controls_api.first_project_id},
    )

    assert missing_action.status_code == missing_revision.status_code == 404
    assert missing_action.json() == missing_revision.json() == {
        "detail": "Provider action is unavailable",
    }


def test_project_and_tenant_isolation_fail_closed(controls_api):
    foreign_through_owned_scope = controls_api.client.get(
        "/provider-actions/google-task-foreign/revisions/1",
        params={"project_id": controls_api.first_project_id},
    )
    forbidden_project = controls_api.client.get(
        "/provider-actions",
        params={"project_id": controls_api.second_project_id},
    )

    assert foreign_through_owned_scope.status_code == 404
    assert forbidden_project.status_code == 403
    assert "google-task-foreign" not in foreign_through_owned_scope.text


def test_expired_approval_is_reported_without_mutation_or_internal_fields(controls_api):
    with controls_api.sessions.begin() as db:
        row = db.get(ProviderActionApproval, "approval-1-google-task-revised-2")
        row.state = "EXPIRED"

    response = controls_api.client.get(
        "/provider-actions/google-task-revised/revisions/2/status",
        params={"project_id": controls_api.first_project_id},
    )

    assert response.status_code == 200
    assert response.json()["approval_status"] == "expired"
    assert response.json()["safe_reason"] == "approval_expired"
    with controls_api.sessions() as db:
        assert db.get(ProviderActionApproval, "approval-1-google-task-revised-2").state == "EXPIRED"
