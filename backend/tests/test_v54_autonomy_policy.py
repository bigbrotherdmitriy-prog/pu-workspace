"""Synthetic acceptance scenario B and fail-closed autonomy policy bypasses."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.autonomy_policy import (
    ActionCandidate, AutonomyConflict, AutonomyDenied, AutonomyPolicyService,
    PolicyAssignmentCommand, PolicyRevokeCommand,
)
from app.core.v54_authority import AuthorityResolver, PILOT_SCOPE
from app.core.auth import require_user
from app.core.v54_dto import canonical_hash
from app.database import Base
from app.database import get_db
from app.api.autonomy_policy import get_autonomy_clock
from app.main import app
from app.models.audit_log import AuditLog
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import ActionPolicy, AuditExtension
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import NOW


OWNER_PERMISSIONS = [
    "action.freeze", "audit.append", "authority.manage", "autonomy.policy.manage",
]


def _seed(db):
    db.add_all([
        Organization(id=1, name="Synthetic tenant"),
        Organization(id=2, name="Other tenant"),
        User(id=2, name="Owner", email="owner-autonomy@example.test", is_admin=False),
        User(id=3, name="Manager", email="manager-autonomy@example.test", is_admin=False),
        User(id=4, name="Global admin", email="admin-autonomy@example.test", is_admin=True),
    ])
    db.flush()
    db.add_all([
        Project(id=4, name="Synthetic project", organization_id=1),
        Project(id=9, name="Other project", organization_id=2),
    ])
    db.flush()
    db.add_all([
        ProjectMember(project_id=4, user_id=2, role="owner"),
        ProjectMember(project_id=4, user_id=3, role="manager"),
        ProjectMember(project_id=4, user_id=4, role="owner"),
    ])
    db.add_all([
        AuthorityState(
            organization_id=1, project_id=4, principal_kind="user", principal_id="2",
            scope=PILOT_SCOPE, membership_role="owner", permissions=OWNER_PERMISSIONS,
            state="active", authority_epoch=7, record_version=1,
            valid_until=NOW + timedelta(hours=1), updated_at=NOW, updated_by_user_id=2,
        ),
        AuthorityState(
            organization_id=1, project_id=4, principal_kind="user", principal_id="3",
            scope=PILOT_SCOPE, membership_role="manager",
            permissions=OWNER_PERMISSIONS, state="active", authority_epoch=5, record_version=1,
            valid_until=NOW + timedelta(hours=1), updated_at=NOW, updated_by_user_id=2,
        ),
        AuthorityState(
            organization_id=1, project_id=4, principal_kind="service", principal_id="model-worker",
            scope=PILOT_SCOPE, membership_role=None,
            permissions=["action.freeze", "autonomy.policy.manage"], state="active",
            authority_epoch=3, record_version=1,
            valid_until=NOW + timedelta(hours=1), updated_at=NOW, updated_by_user_id=2,
        ),
    ])


@pytest.fixture
def world(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'autonomy.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as db:
        _seed(db)
    service = AutonomyPolicyService(
        authority=AuthorityResolver(clock=lambda: NOW), clock=lambda: NOW,
    )
    try:
        yield sessions, service
    finally:
        engine.dispose()


def _assignment(**changes):
    values = dict(
        expected_policy_id=None, expected_revision=0, expected_policy_hash=None,
        expected_authority_epoch=7, create_internal_task="AUTO",
        send_external_message="CONFIRM", valid_until=NOW + timedelta(minutes=30),
    )
    values.update(changes)
    return PolicyAssignmentCommand(**values)


def _candidate(action_type="task.internal.create", **changes):
    values = dict(
        action_type=action_type, stage="EXECUTE", risk="LOW", reversal="COMPENSATABLE",
        effects=("internal_task.create", "task_history.append"),
        envelope_sha256=canonical_hash({"sealed": "envelope-a"}),
        payload_sha256=canonical_hash({"title": "Synthetic task"}),
    )
    if action_type == "message.external.send":
        values.update(risk="HIGH", reversal="IRREVERSIBLE", effects=("message.external.send",))
    values.update(changes)
    return ActionCandidate(**values)


def _install(sessions, service):
    with sessions.begin() as db:
        return service.assign(db, scope=scope(2), command=_assignment())


def test_scenario_b_internal_create_auto_external_send_confirm_independent_of_requested_model_mode(world):
    sessions, service = world
    policy = _install(sessions, service)
    with sessions.begin() as db:
        internal = service.decide(db, scope=scope(2), candidate=_candidate())
        external = service.decide(db, scope=scope(2), candidate=_candidate("message.external.send"))
    assert policy.create_internal_task == internal.mode == "AUTO"
    assert policy.send_external_message == external.mode == "CONFIRM"
    assert internal.policy == policy.policy and internal.policy_authority_epoch == 7
    with pytest.raises(ValidationError):
        ActionCandidate.model_validate({**_candidate().model_dump(mode="json"), "requested_mode": "AUTO"})


@pytest.mark.parametrize("candidate", [
    _candidate(risk="HIGH"),
    _candidate(action_type="finance.pay", risk="HIGH", reversal="IRREVERSIBLE", effects=("finance.pay",)),
    _candidate(action_type="legal.sign", risk="HIGH", reversal="IRREVERSIBLE", effects=("legal.sign",)),
    _candidate(action_type="destructive.delete", risk="HIGH", reversal="IRREVERSIBLE", effects=("destructive.delete",)),
    _candidate(action_type="access.grant", risk="HIGH", reversal="IRREVERSIBLE", effects=("access.grant",)),
])
def test_high_risk_and_protected_capabilities_never_auto(world, candidate):
    sessions, service = world
    _install(sessions, service)
    with sessions.begin() as db:
        assert service.decide(db, scope=scope(2), candidate=candidate).mode == "CONFIRM"


def test_unknown_is_deny_and_advisory_defaults_assist(world):
    sessions, service = world
    with sessions.begin() as db:
        assert service.decide(db, scope=scope(2), candidate=_candidate(action_type="unknown.execute")).mode == "DENY"
        assert service.decide(db, scope=scope(2), candidate=_candidate(stage="ANALYZE")).mode == "ASSIST"


def test_only_exact_human_owner_authority_can_assign(world):
    sessions, service = world
    with pytest.raises(AutonomyDenied):
        with sessions.begin() as db:
            service.assign(db, scope=scope(3), command=_assignment(expected_authority_epoch=5))
    with pytest.raises(AutonomyDenied):
        with sessions.begin() as db:
            service.assign(db, scope=scope(4), command=_assignment())
    raw = scope(2).model_dump(mode="json")
    raw["actor"] = {**raw["actor"], "type": "service", "id": {"kind": "uuid", "value": raw["actor"]["id"]["value"]}}
    from app.core.v54_interfaces import RequestScope
    with pytest.raises(ValidationError):
        RequestScope.model_validate(raw)


def test_policy_cas_stale_epoch_and_duplicate_policy_fail_closed(world):
    sessions, service = world
    first = _install(sessions, service)
    stale = _assignment(expected_policy_id=first.policy.ref.id.value, expected_revision=1,
                        expected_policy_hash="0" * 64)
    with pytest.raises(AutonomyConflict, match="stale_policy"):
        with sessions.begin() as db:
            service.assign(db, scope=scope(2), command=stale)
    with pytest.raises(AutonomyConflict, match="stale_authority_epoch"):
        with sessions.begin() as db:
            service.assign(db, scope=scope(2), command=_assignment(expected_authority_epoch=6))
    with pytest.raises(AutonomyConflict, match="policy_exists"):
        with sessions.begin() as db:
            service.assign(db, scope=scope(2), command=_assignment())


@pytest.mark.parametrize("field,value", [
    ("action_type", "message.external.send"),
    ("payload_sha256", "f" * 64),
    ("envelope_sha256", "e" * 64),
    ("effects", ("internal_task.create",)),
])
def test_altered_action_or_payload_cannot_reuse_auto_decision(world, field, value):
    sessions, service = world
    _install(sessions, service)
    candidate = _candidate()
    with sessions.begin() as db:
        decision = service.decide(db, scope=scope(2), candidate=candidate)
    altered = candidate.model_copy(update={field: value})
    with pytest.raises(AutonomyConflict, match="stale_action_binding"):
        with sessions.begin() as db:
            service.recheck(db, scope=scope(2), candidate=altered, decision=decision)


def test_authority_rotation_and_policy_revoke_invalidate_old_auto_decision(world):
    sessions, service = world
    policy = _install(sessions, service)
    candidate = _candidate()
    with sessions.begin() as db:
        decision = service.decide(db, scope=scope(2), candidate=candidate)
    with sessions.begin() as db:
        AuthorityResolver(clock=lambda: NOW).change(
            db, scope=scope(2), principal_id=2, membership_role="owner",
            permissions=OWNER_PERMISSIONS, state="active", expected_epoch=7,
        )
    with pytest.raises(AutonomyDenied):
        with sessions.begin() as db:
            service.recheck(db, scope=scope(2), candidate=candidate, decision=decision)

    # A fresh world demonstrates explicit revocation independently of rotation.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    fresh = sessionmaker(engine, expire_on_commit=False)
    with fresh.begin() as db:
        _seed(db)
    fresh_service = AutonomyPolicyService(authority=AuthorityResolver(clock=lambda: NOW), clock=lambda: NOW)
    installed = _install(fresh, fresh_service)
    with fresh.begin() as db:
        old = fresh_service.decide(db, scope=scope(2), candidate=candidate)
    command = PolicyRevokeCommand(
        expected_policy_id=installed.policy.ref.id.value, expected_revision=installed.policy.value,
        expected_policy_hash=installed.policy_sha256, expected_authority_epoch=7,
    )
    with fresh.begin() as db:
        revoked = fresh_service.revoke(db, scope=scope(2), command=command)
        assert revoked.enabled is False and revoked.create_internal_task == "CONFIRM"
    with pytest.raises(AutonomyConflict, match="stale_policy_decision"):
        with fresh.begin() as db:
            fresh_service.recheck(db, scope=scope(2), candidate=candidate, decision=old)
    engine.dispose()


def test_policy_history_and_audit_are_append_only_and_pii_free(world):
    sessions, service = world
    first = _install(sessions, service)
    command = _assignment(
        expected_policy_id=first.policy.ref.id.value, expected_revision=first.policy.value,
        expected_policy_hash=first.policy_sha256, create_internal_task="CONFIRM",
    )
    with sessions.begin() as db:
        second = service.assign(db, scope=scope(2), command=command)
    with sessions.begin() as db:
        assert second.policy.value == 2
        assert db.scalar(select(func.count()).select_from(ActionPolicy)) == 2
        audits = list(db.scalars(select(AuditLog).where(AuditLog.action == "v54.AUTONOMY_POLICY_CHANGED")))
        assert len(audits) == 2 and all(item.details is None for item in audits)
        extensions = list(db.scalars(select(AuditExtension).where(AuditExtension.subject_type == "policy")))
        assert [item.sequence for item in extensions] == [1, 2]
        serialized = " ".join(str(item.__dict__) for item in audits + extensions)
        assert "owner-autonomy@example.test" not in serialized


def test_http_adapter_assigns_and_decides_without_admin_fallback(world):
    sessions, _ = world

    def session_override():
        with sessions() as db:
            yield db

    def owner_override():
        with sessions() as db:
            return db.get(User, 2)

    app.dependency_overrides[get_db] = session_override
    app.dependency_overrides[require_user] = owner_override
    app.dependency_overrides[get_autonomy_clock] = lambda: (lambda: NOW)
    try:
        client = TestClient(app)
        response = client.put("/api/v54/projects/4/autonomy-policy", json={
                "expected_policy_id": None,
                "expected_revision": 0,
                "expected_policy_hash": None,
                "expected_authority_epoch": 7,
                "create_internal_task": "AUTO",
                "send_external_message": "CONFIRM",
                "valid_until": (NOW + timedelta(minutes=30)).isoformat(),
        })
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        decision = client.post("/api/v54/projects/4/autonomy-policy/decide",
                               json=_candidate().model_dump(mode="json"))
        assert decision.status_code == 200 and decision.json()["mode"] == "AUTO"

        def admin_override():
            with sessions() as db:
                return db.get(User, 4)
        app.dependency_overrides[require_user] = admin_override
        denied = client.get("/api/v54/projects/4/autonomy-policy")
        assert denied.status_code == 404 and denied.json() == {"detail": "resource_unavailable"}
    finally:
        app.dependency_overrides.clear()
