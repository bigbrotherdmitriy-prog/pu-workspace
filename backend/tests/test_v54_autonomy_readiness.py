from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.autonomy_readiness import exhausted_quota_reasons, project_autonomy_readiness
from app.autonomy_policy import AutonomyPolicyService, PolicyAssignmentCommand
from app.core.auth import require_user
from app.core.v54_authority import AuthorityResolver, PILOT_OPERATIONS, PILOT_SCOPE
from app.core.v54_interfaces import RequestScope
from app.database import Base, get_db
from app.main import app
from app.models.integration_credential import IntegrationCredential
from app.models.mailbox_identity import (
    MailboxCredentialGeneration, MailboxCutoverFlags, MailboxProjectCohort,
)
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import ConnectionIdentity, MailConnection
from v54_pilot_fixture import ref, uid


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _scope(user_id=2):
    return RequestScope(
        tenant={"kind": "int", "value": "1"}, actor=ref("user", user_id),
        project=ref("project", 10), correlation_id="readiness-test",
    )


@pytest.fixture
def readiness_world(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'readiness.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    for key, value in {
        "PU_V54_AUTO_PILOT_ENABLED": "true",
        "PU_V54_AUTO_INTENT_PRODUCER_ENABLED": "true",
        "PU_V54_AUTO_NOTIFICATION_ENABLED": "true",
        "PU_V54_AUTO_PILOT_PROJECT_ID": "10",
        "PU_V54_AUTO_PILOT_OWNER_USER_ID": "2",
    }.items():
        monkeypatch.setenv(key, value)
    with sessions.begin() as db:
        db.add(Organization(id=1, name="Tenant"))
        db.add_all([
            User(id=2, name="Owner", email="owner-readiness@example.test"),
            User(id=3, name="Manager", email="manager-readiness@example.test"),
            User(id=4, name="Viewer", email="viewer-readiness@example.test"),
            User(id=5, name="Global admin", email="admin-readiness@example.test", is_admin=True),
        ])
        db.flush()
        db.add_all([
            Project(id=10, name="Pilot", organization_id=1),
            Project(id=11, name="Other", organization_id=1),
        ])
        db.flush()
        db.add_all([
            ProjectMember(project_id=10, user_id=2, role="owner"),
            ProjectMember(project_id=10, user_id=3, role="manager"),
            ProjectMember(project_id=10, user_id=4, role="viewer"),
        ])
        authority = AuthorityState(
            organization_id=1, project_id=10, principal_kind="user", principal_id="2",
            scope=PILOT_SCOPE, membership_role="owner", permissions=sorted(PILOT_OPERATIONS),
            state="active", authority_epoch=1, record_version=1,
            valid_until=NOW + timedelta(days=2), updated_at=NOW, updated_by_user_id=2,
        )
        db.add(authority)
        db.flush()
        AutonomyPolicyService(
            authority=AuthorityResolver(clock=lambda: NOW), clock=lambda: NOW,
        ).assign(db, scope=_scope(), command=PolicyAssignmentCommand(
            expected_policy_id=None, expected_revision=0, expected_policy_hash=None,
            expected_authority_epoch=1, create_internal_task="AUTO",
            create_internal_notification="AUTO", valid_until=NOW + timedelta(hours=24),
        ))
        credential = IntegrationCredential(
            project_id=10, provider="google_drive", capability="storage",
            access_token="encrypted-placeholder", account_external_id="redacted-account",
        )
        db.add(credential)
        db.flush()
        identity = ConnectionIdentity(
            id=uid(901), organization_id=1, provider="google_workspace",
            account_key="must-not-leak@example.test", state="verified", binding_epoch=1,
            record_version=1, credential_id=credential.id, credential_generation=1,
            verified_at=NOW,
        )
        mail = MailConnection(
            id=uid(902), organization_id=1, identity_id=identity.id,
            namespace="gmail", state="active", record_version=1,
        )
        db.add_all([identity, mail])
        db.flush()
        db.add_all([
            MailboxCredentialGeneration(
                organization_id=1, connection_identity_id=identity.id, generation=1,
                binding_epoch=1, integration_credential_id=credential.id,
                state="active", verified_at=NOW,
            ),
            MailboxCutoverFlags(
                organization_id=1, mail_connection_id=mail.id, credential_generation=1,
                shadow_write=True, shadow_read_compare=True, pilot_write=True,
                primary_read=False, actions=False, record_version=1,
            ),
            MailboxProjectCohort(
                organization_id=1, project_id=10, mail_connection_id=mail.id,
                credential_generation=1, enabled=True, record_version=1,
                changed_by_user_id=2, changed_at=NOW,
            ),
        ])

    current = {"user_id": 2}
    def override_db():
        with sessions() as db:
            yield db
    def override_user():
        with sessions() as db:
            return db.get(User, current["user_id"])
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_user] = override_user
    try:
        yield sessions, current, TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_owner_and_manager_get_redacted_read_only_projection(readiness_world):
    sessions, current, client = readiness_world
    with sessions() as db:
        before = sum(len(db.scalars(select(model)).all()) for model in (
            AuthorityState, MailboxProjectCohort, MailboxCutoverFlags,
        ))
    for user_id in (2, 3):
        current["user_id"] = user_id
        response = client.get("/api/v54/projects/10/autonomy-readiness")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["overall"]["status"] == "DEGRADED"
        assert body["policy"]["external_message_mode"] == "CONFIRM"
        assert body["policy"]["history"][0]["revision"] == 1
        assert body["mailbox"]["cutover"]["actions"] is False
        assert body["runtime"]["component_alignment"] == "unverified"
        serialized = response.text
        assert "must-not-leak" not in serialized
        assert "encrypted-placeholder" not in serialized
        assert "last_error" not in serialized
    with sessions() as db:
        after = sum(len(db.scalars(select(model)).all()) for model in (
            AuthorityState, MailboxProjectCohort, MailboxCutoverFlags,
        ))
    assert before == after


def test_readiness_route_has_no_mutation_method(readiness_world):
    _sessions, _current, client = readiness_world
    assert client.post("/api/v54/projects/10/autonomy-readiness", json={}).status_code == 405


@pytest.mark.parametrize("user_id", [4, 5])
def test_viewer_and_global_admin_without_membership_are_denied(readiness_world, user_id):
    _sessions, current, client = readiness_world
    current["user_id"] = user_id
    assert client.get("/api/v54/projects/10/autonomy-readiness").status_code == 404


def test_project_without_policy_is_indistinguishable(readiness_world):
    sessions, current, client = readiness_world
    with sessions.begin() as db:
        db.add(ProjectMember(project_id=11, user_id=2, role="owner"))
    current["user_id"] = 2
    assert client.get("/api/v54/projects/11/autonomy-readiness").status_code == 404
    assert client.get("/api/v54/projects/999/autonomy-readiness").status_code == 404


def test_revoked_and_epoch_mismatch_fail_closed(readiness_world):
    sessions, _current, client = readiness_world
    with sessions.begin() as db:
        row = db.scalar(select(AuthorityState).where(AuthorityState.project_id == 10))
        row.state = "revoked"
        row.authority_epoch = 2
        row.record_version = 2
        row.updated_at = NOW + timedelta(minutes=1)
    body = client.get("/api/v54/projects/10/autonomy-readiness").json()
    assert body["overall"]["status"] == "BLOCKED"
    assert "authority_revoked_expired_or_mismatched" in body["overall"]["blockers"]


def test_expired_policy_and_authority_are_blocked(readiness_world):
    sessions, _current, _client = readiness_world
    with sessions() as db:
        body = project_autonomy_readiness(
            db, db.get(Project, 10), now=NOW + timedelta(days=3),
        )
    assert body["overall"]["status"] == "BLOCKED"
    assert "policy_disabled_or_expired" in body["overall"]["blockers"]
    assert "authority_revoked_expired_or_mismatched" in body["overall"]["blockers"]


def test_runtime_scope_mismatch_and_mailbox_stale_are_blocked(readiness_world, monkeypatch):
    sessions, _current, client = readiness_world
    monkeypatch.setenv("PU_V54_AUTO_PILOT_PROJECT_ID", "11")
    with sessions.begin() as db:
        row = db.scalar(select(MailboxCutoverFlags))
        row.actions = True  # invalid lattice: actions requires primary_read
        row.record_version = 2
    body = client.get("/api/v54/projects/10/autonomy-readiness").json()
    assert body["overall"]["status"] == "BLOCKED"
    assert "runtime_project_scope_mismatch" in body["overall"]["blockers"]
    assert "mailbox_cutover_not_ready" in body["overall"]["blockers"]


def test_valid_shadow_only_mailbox_is_not_producer_ready(readiness_world):
    sessions, _current, client = readiness_world
    with sessions.begin() as db:
        row = db.scalar(select(MailboxCutoverFlags))
        row.pilot_write = False
        row.record_version = 2
    body = client.get("/api/v54/projects/10/autonomy-readiness").json()
    assert body["mailbox"]["valid"] is True
    assert body["mailbox"]["producer_ready"] is False
    assert body["mailbox"]["state"] == "valid_not_pilot"
    assert "mailbox_cutover_not_ready" in body["overall"]["blockers"]


def test_quota_boundary_is_visible_without_recipient_identity(readiness_world, monkeypatch):
    _sessions, _current, client = readiness_world
    monkeypatch.setenv("PU_V54_AUTO_PILOT_HOURLY_QUOTA", "2")
    body = client.get("/api/v54/projects/10/autonomy-readiness").json()
    assert body["quotas"]["task_hourly"] == {"used": 0, "limit": 2}
    assert set(body["quotas"]["notification_recipient_daily"]) == {"max_used", "limit"}


def test_quota_boundary_degrades_exactly_at_limit():
    below = {"task_hourly": {"used": 2, "limit": 3}}
    boundary = {"task_hourly": {"used": 3, "limit": 3}}
    assert exhausted_quota_reasons(below) == []
    assert exhausted_quota_reasons(boundary) == ["task_hourly_exhausted"]
