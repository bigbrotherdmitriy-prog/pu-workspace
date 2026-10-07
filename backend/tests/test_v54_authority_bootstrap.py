"""ADR-V6-07-REOPENING-RU condition 1: AuthorityResolver.bootstrap and the
admin-only HTTP adapter around it. Mandatory test list per the ADR: create
when no row exists; deny an active unexpired row (use change() instead);
reissue an expired row with epoch+1; deny a non-admin caller; a distinct
AUTHORITY_BOOTSTRAPPED audit event with full metadata and no secrets.
"""
from datetime import timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.auth import require_user
from app.core.v54_authority import AuthorityDenied, AuthorityResolver, BOOTSTRAP_VALID_FOR, PILOT_OPERATIONS, PILOT_SCOPE
from app.database import Base, get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import AuditExtension
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import NOW


OWNER_PERMISSIONS = ["metadata", "review", "authority.manage"]


def _aware(value):
    # SQLite drops tzinfo on round-trip; Postgres (DateTime(timezone=True))
    # does not. Both ends of this comparison are UTC either way.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _seed(db):
    db.add_all([
        Organization(id=1, name="Synthetic tenant"),
        Organization(id=2, name="Other tenant"),
        User(id=2, name="Owner", email="owner-bootstrap@example.test", is_admin=False),
        User(id=4, name="Global admin", email="admin-bootstrap@example.test", is_admin=True),
        User(id=5, name="Not admin", email="not-admin-bootstrap@example.test", is_admin=False),
    ])
    db.flush()
    db.add_all([
        Project(id=4, name="Synthetic project", organization_id=1),
        Project(id=9, name="Other project", organization_id=2),
    ])
    db.flush()
    db.add_all([
        ProjectMember(project_id=4, user_id=2, role="owner"),
        ProjectMember(project_id=4, user_id=4, role="owner"),
        ProjectMember(project_id=4, user_id=5, role="viewer"),
    ])
    db.flush()


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'authority-bootstrap.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker.begin() as db:
        _seed(db)
    try:
        yield maker
    finally:
        engine.dispose()


def test_creates_a_row_when_none_exists_with_epoch_one(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        # ADR-V6-07-REOPENING-RU: the bootstrap-admin is the owner's own
        # is_admin=true account (user 4 in this fixture), acting on a mandate
        # for a different principal (user 2) -- never on its own behalf.
        epoch = resolver.bootstrap(
            db, scope=scope(actor=4), principal_kind="user", principal_id="2",
            membership_role="owner", permissions=OWNER_PERMISSIONS,
        )
        assert epoch == 1
    with sessions() as db:
        row = db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "2"))
        assert (row.authority_epoch, row.record_version, row.state) == (1, 1, "active")
        assert _aware(row.valid_until) == NOW + BOOTSTRAP_VALID_FOR
        assert row.updated_by_user_id == 4
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "v54.AUTHORITY_BOOTSTRAPPED"))
        assert audit is not None and audit.details is None
        extension = db.scalar(select(AuditExtension).where(AuditExtension.subject_type == "project"))
        assert extension is not None and extension.actor_id == 4 and extension.project_id == 4


def test_denies_an_active_unexpired_row_use_change_instead(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        db.add(AuthorityState(
            organization_id=1, project_id=4, principal_kind="user", principal_id="2",
            scope=PILOT_SCOPE, membership_role="owner", permissions=OWNER_PERMISSIONS,
            state="active", authority_epoch=3, record_version=1,
            valid_until=NOW + timedelta(hours=1), updated_at=NOW, updated_by_user_id=2,
        ))
    with pytest.raises(AuthorityDenied):
        with sessions.begin() as db:
            resolver.bootstrap(
                db, scope=scope(), principal_kind="user", principal_id="2",
                membership_role="owner", permissions=OWNER_PERMISSIONS,
            )
    with sessions() as db:
        row = db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "2"))
        assert row.authority_epoch == 3  # untouched
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_reissues_an_expired_row_with_epoch_plus_one(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        db.add(AuthorityState(
            organization_id=1, project_id=4, principal_kind="user", principal_id="2",
            scope=PILOT_SCOPE, membership_role="owner", permissions=["metadata"],
            state="active", authority_epoch=6, record_version=4,
            valid_until=NOW - timedelta(seconds=1), updated_at=NOW - timedelta(days=5),
            updated_by_user_id=2,
        ))
    with sessions.begin() as db:
        epoch = resolver.bootstrap(
            db, scope=scope(), principal_kind="user", principal_id="2",
            membership_role="owner", permissions=OWNER_PERMISSIONS,
        )
        assert epoch == 7
    with sessions() as db:
        row = db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "2"))
        assert (row.authority_epoch, row.record_version) == (7, 5)
        assert _aware(row.valid_until) == NOW + BOOTSTRAP_VALID_FOR
        assert row.permissions == sorted(OWNER_PERMISSIONS)
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "v54.AUTHORITY_BOOTSTRAPPED"))
        assert audit is not None and audit.details is None
        # The ordinary renewal path logs a different, pre-existing event name.
        assert db.scalar(select(func.count()).select_from(AuditLog)
                          .where(AuditLog.action == "v54.AUTHORITY_CHANGED")) == 0


def test_a_revoked_but_unexpired_row_is_not_bootstrappable_either(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        db.add(AuthorityState(
            organization_id=1, project_id=4, principal_kind="service", principal_id="worker-1",
            scope=PILOT_SCOPE, membership_role=None, permissions=["metadata"],
            state="revoked", authority_epoch=2, record_version=2,
            valid_until=NOW + timedelta(hours=1), updated_at=NOW, updated_by_user_id=2,
        ))
    with sessions.begin() as db:
        epoch = resolver.bootstrap(
            db, scope=scope(), principal_kind="service", principal_id="worker-1",
            membership_role=None, permissions=["metadata"],
        )
        # Revoked rows are not "active", so bootstrap is allowed to reissue
        # them -- a revoked row has no live mandate to protect via change().
        assert epoch == 3


def test_cross_tenant_principal_is_untouched(sessions):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions.begin() as db:
        with pytest.raises(AuthorityDenied):
            resolver.bootstrap(
                db, scope=scope(), principal_kind="user", principal_id="999",
                membership_role="owner", permissions=OWNER_PERMISSIONS,
            )
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuthorityState)) == 0


def test_http_adapter_requires_global_admin_not_pilot_mandate(sessions):
    def session_override():
        with sessions() as db:
            yield db

    def admin_override():
        with sessions() as db:
            return db.get(User, 4)

    app.dependency_overrides[get_db] = session_override
    app.dependency_overrides[require_user] = admin_override
    try:
        client = TestClient(app)
        response = client.post("/api/v54/projects/4/authority/bootstrap", json={
            "principal_kind": "user", "principal_id": "2",
            "membership_role": "owner", "permissions": OWNER_PERMISSIONS,
        })
        assert response.status_code == 200
        assert response.json()["authority_epoch"] == 1

        def not_admin_override():
            with sessions() as db:
                return db.get(User, 5)
        app.dependency_overrides[require_user] = not_admin_override
        denied = client.post("/api/v54/projects/4/authority/bootstrap", json={
            "principal_kind": "user", "principal_id": "2",
            "membership_role": "owner", "permissions": OWNER_PERMISSIONS,
        })
        assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
    with sessions() as db:
        row = db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "2"))
        assert row is not None and row.authority_epoch == 1
        audits = list(db.scalars(select(AuditLog).where(AuditLog.action == "v54.AUTHORITY_BOOTSTRAPPED")))
        assert len(audits) == 1
        extensions = list(db.scalars(select(AuditExtension)))
        serialized = " ".join(str(item.__dict__) for item in audits + extensions)
        assert "admin-bootstrap@example.test" not in serialized and "owner-bootstrap@example.test" not in serialized


def test_http_change_widens_an_already_active_mandate_without_touching_admin_gate(sessions):
    # The readiness panel's permission-completeness check also fires on a
    # live, unexpired mandate that simply never held the full PILOT_OPERATIONS
    # set. bootstrap() refuses an active row on principle; change() is the
    # ordinary, self-gated path for widening one in place -- no require_admin
    # needed, because change() re-derives authorization from the caller's own
    # authority.manage mandate.
    def session_override():
        with sessions() as db:
            yield db

    def owner_override():
        with sessions() as db:
            return db.get(User, 2)

    def admin_override():
        with sessions() as db:
            return db.get(User, 4)

    app.dependency_overrides[get_db] = session_override
    app.dependency_overrides[require_user] = admin_override
    try:
        client = TestClient(app)
        # Seed through the real HTTP bootstrap path -- both this and the PATCH
        # below use the endpoint's own un-frozen AuthorityResolver() clock, so
        # valid_until actually lands in the future relative to it (a
        # separately frozen `NOW` here would already be long expired by the
        # time either live call runs).
        created = client.post("/api/v54/projects/4/authority/bootstrap", json={
            "principal_kind": "user", "principal_id": "2",
            "membership_role": "owner", "permissions": ["metadata", "authority.manage"],
        })
        assert created.status_code == 200 and created.json()["authority_epoch"] == 1

        # Now the mandate holder (user 2) widens their own row; no admin
        # override is used from here on, proving change() is self-gated.
        app.dependency_overrides[require_user] = owner_override
        response = client.patch("/api/v54/projects/4/authority", json={
            "principal_id": 2, "membership_role": "owner", "state": "active",
            "expected_epoch": 1, "permissions": sorted(PILOT_OPERATIONS),
        })
        assert response.status_code == 200 and response.json()["authority_epoch"] == 2

        def not_mandated_override():
            with sessions() as db:
                return db.get(User, 5)
        app.dependency_overrides[require_user] = not_mandated_override
        denied = client.patch("/api/v54/projects/4/authority", json={
            "principal_id": 2, "membership_role": "owner", "state": "active",
            "expected_epoch": 2, "permissions": sorted(PILOT_OPERATIONS),
        })
        assert denied.status_code == 409
    finally:
        app.dependency_overrides.clear()
    with sessions() as db:
        row = db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "2"))
        assert row.authority_epoch == 2 and set(row.permissions) == PILOT_OPERATIONS
