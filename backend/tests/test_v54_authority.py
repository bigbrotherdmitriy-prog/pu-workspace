"""DB authority contract tests. SQLite tests do not prove lock concurrency."""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models
from app.core.v54_authority import AuthorityDenied, AuthorityResolver, PILOT_SCOPE
from app.database import Base
from app.models.audit_log import AuditLog
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import AuditExtension
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import NOW
from test_v54_pilot_integration import integrated, claimed, execute, prepare


REQUESTER = [
    "metadata", "review", "dispatch", "audit", "audit.append", "action.dispatch",
    "action.execute", "action.receipt.read", "task.assign", "task.assignee", "authority.manage",
]
REVIEWER = ["metadata", "review", "dispatch", "audit", "audit.append", "claim.review", "action.approve", "action.revoke"]


def seed_authority(db):
    db.add_all([
        Organization(id=1, name="Synthetic tenant"),
        Organization(id=2, name="Other tenant"),
        User(id=2, name="Requester", email="requester-authority@example.test", is_admin=False),
        User(id=3, name="Reviewer", email="reviewer-authority@example.test", is_admin=False),
        User(id=4, name="Global admin", email="admin-authority@example.test", is_admin=True),
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
            scope=PILOT_SCOPE, membership_role="owner", permissions=REQUESTER,
            state="active", authority_epoch=1, record_version=1,
            valid_until=NOW + timedelta(hours=1), updated_at=NOW,
        ),
        AuthorityState(
            organization_id=1, project_id=4, principal_kind="user", principal_id="3",
            scope=PILOT_SCOPE, membership_role="manager", permissions=REVIEWER,
            state="active", authority_epoch=1, record_version=1,
            valid_until=NOW + timedelta(hours=1), updated_at=NOW,
        ),
        AuthorityState(
            organization_id=1, project_id=4, principal_kind="service", principal_id="worker-1",
            scope=PILOT_SCOPE, membership_role=None,
            permissions=["action.execute", "action.approve"], state="active",
            authority_epoch=1, record_version=1,
            valid_until=NOW + timedelta(hours=1), updated_at=NOW,
        ),
    ])
    db.flush()


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'authority.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker.begin() as db:
        seed_authority(db)
    try:
        yield maker
    finally:
        engine.dispose()


def test_snapshot_survives_session_restart_and_unknown_denies(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        first = resolver.require(db, scope(), "action.execute", NOW)
    with sessions.begin() as db:
        loaded = resolver.require(db, scope(), "action.execute", NOW)
        assert loaded == first and loaded.authority_epoch == 1
        with pytest.raises(AuthorityDenied, match="resource_unavailable"):
            resolver.require(db, scope(), "not.a.permission", NOW)


def test_direct_membership_role_change_invalidates_snapshot(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        resolver.require(db, scope(3), "action.approve", NOW)
        db.scalar(select(ProjectMember).where(ProjectMember.user_id == 3)).role = "viewer"
    with pytest.raises(AuthorityDenied):
        with sessions.begin() as db:
            resolver.require(db, scope(3), "action.approve", NOW)


def test_role_change_increments_epoch_and_audits_without_details(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        epoch = resolver.change(
            db, scope=scope(), principal_id=3, membership_role="viewer",
            permissions=["metadata"], state="active", expected_epoch=1,
        )
        assert epoch == 2
    with sessions() as db:
        row = db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "3"))
        assert row.authority_epoch == 2 and row.record_version == 2
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "v54.AUTHORITY_CHANGED"))
        assert audit and audit.details is None
        assert db.scalar(select(func.count()).select_from(AuditExtension)) == 1
        with pytest.raises(AuthorityDenied):
            resolver.require(db, scope(3), "action.approve", NOW)


def test_change_rollback_includes_membership_epoch_and_audit(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with pytest.raises(RuntimeError):
        with sessions.begin() as db:
            resolver.change(
                db, scope=scope(), principal_id=3, membership_role="viewer",
                permissions=["metadata"], state="revoked", expected_epoch=1,
            )
            raise RuntimeError("synthetic rollback")
    with sessions() as db:
        row = db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "3"))
        member = db.scalar(select(ProjectMember).where(ProjectMember.user_id == 3))
        assert (row.authority_epoch, row.state, member.role) == (1, "active", "manager")
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_cross_tenant_project_admin_and_service_fail_closed(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        with pytest.raises(AuthorityDenied):
            resolver.require(db, scope(4), "action.approve", NOW)  # is_admin has no mandate
        with pytest.raises(AuthorityDenied):
            resolver.require_principal(
                db, tenant_id=1, project_id=4, principal_kind="service", principal_id="worker-1",
                operation="action.approve", now=NOW,
            )
        with pytest.raises(AuthorityDenied):
            resolver.require_principal(
                db, tenant_id=2, project_id=9, principal_kind="user", principal_id="3",
                operation="action.approve", now=NOW,
            )


def test_expired_revoked_and_cas_mismatch_deny(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        reviewer = db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "3"))
        reviewer.valid_until = NOW - timedelta(seconds=1)
        reviewer.authority_epoch += 1
        reviewer.record_version += 1
        reviewer.updated_at = NOW + timedelta(seconds=1)
    with pytest.raises(AuthorityDenied):
        with sessions.begin() as db:
            resolver.require(db, scope(3), "action.approve", NOW)
    with pytest.raises(AuthorityDenied):
        with sessions.begin() as db:
            resolver.change(
                db, scope=scope(), principal_id=3, membership_role="manager",
                permissions=REVIEWER, state="revoked", expected_epoch=99,
            )


def test_authenticated_actor_is_scope_not_untrusted_payload():
    # RequestScope is server-only and rejects service or payload-style actor data.
    raw = scope().model_dump(mode="json")
    raw["actor"] = {**raw["actor"], "type": "service"}
    from app.core.v54_interfaces import RequestScope
    with pytest.raises(ValueError):
        RequestScope.model_validate(raw)


def test_approval_from_old_epoch_is_rejected_immediately_before_t2(integrated):
    from app.models.task import Task
    from app.models.v54_pilot import ActionReceipt
    sessions, component, runtime, _ = integrated
    envelope, payload, owner = claimed(integrated)
    with sessions.begin() as db:
        component.policy.authority.change(
            db, scope=scope(), principal_id=3, membership_role="viewer",
            permissions=["metadata"], state="revoked", expected_epoch=1,
        )
    with pytest.raises(ValueError, match="resource_unavailable"):
        runtime.execute(payload, owner)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 0


def test_two_dispatch_attempts_replay_one_business_effect(integrated):
    from app.models.task import Task
    from app.models.v54_pilot import ActionReceipt
    sessions, _, runtime, _ = integrated
    envelope = prepare(integrated)
    first, payload, owner = execute(integrated, envelope)
    assert runtime.execute(payload, owner) == first
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 1
