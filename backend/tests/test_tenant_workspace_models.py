import pytest
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.models.workspace_tenant import TenantWorkspace, TenantWorkspaceMember


def _make_user(db_session, sequence: int) -> User:
    user = User(
        name=f"Пользователь {sequence}",
        email=f"tenant-user-{sequence}@example.test",
        is_admin=False,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_create_tenant_workspace(db_session):
    workspace = TenantWorkspace(name="Acme", slug="acme")
    db_session.add(workspace)
    db_session.flush()

    assert workspace.id is not None
    assert workspace.slug == "acme"
    assert workspace.plan == "start"


def test_tenant_workspace_slug_is_unique(db_session):
    db_session.add(TenantWorkspace(name="Acme", slug="acme"))
    db_session.flush()

    db_session.add(TenantWorkspace(name="Other", slug="acme"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_tenant_workspace_member_pair_unique(db_session):
    user = _make_user(db_session, 1)
    workspace = TenantWorkspace(name="Acme", slug="acme")
    db_session.add(workspace)
    db_session.flush()

    db_session.add(TenantWorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="member"))
    db_session.flush()

    db_session.add(TenantWorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="admin"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_tenant_workspace_member_role_values(db_session):
    user = _make_user(db_session, 1)
    workspace = TenantWorkspace(name="Acme", slug="acme")
    db_session.add(workspace)
    db_session.flush()

    member = TenantWorkspaceMember(user_id=user.id, workspace_id=workspace.id)
    db_session.add(member)
    db_session.flush()

    assert member.role == "member"

    admin = _make_user(db_session, 2)
    member_admin = TenantWorkspaceMember(
        user_id=admin.id,
        workspace_id=workspace.id,
        role="admin",
    )
    db_session.add(member_admin)
    db_session.flush()

    assert member_admin.role == "admin"


# ---------------------------------------------------------------------------
# HTTP tenant-isolation tests for project creation.
#
# These tests build a minimal FastAPI app that mounts only ``projects.router``
# and override ``get_db`` / ``require_user`` with a synthetic, persisted actor.
# They exercise the real HTTP runtime (routing, request parsing, dependency
# wiring) rather than calling route functions directly.
#
# NOTE: These tests are expected to be RED against the current runtime, which
# only checks that the target Organization exists and otherwise defaults to the
# globally-first organization. They are intentionally NOT marked xfail/skip and
# the assertions are not weakened. If the sandbox cannot run TestClient, the
# tests will error at collection/import time and must be reported as UNRUN.
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.core.auth import require_user
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.workspace_tenant import TenantWorkspace, TenantWorkspaceMember
from app.api import projects as projects_api


@pytest.fixture()
def http_env():
    """Isolated in-memory SQLite + minimal app with projects.router.

    Creates a synthetic actor who is an admin member of their OWN workspace and
    owns their OWN organization. A FOREIGN workspace/organization is created
    FIRST so that it has the smallest organization id (the global-first default).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSessionLocal()
    try:
        # FOREIGN tenant first -> smallest organization id.
        foreign_workspace = TenantWorkspace(name="Foreign Co", slug="foreign-co")
        db.add(foreign_workspace)
        db.flush()
        foreign_org = Organization(name="Foreign Org", tenant_workspace_id=foreign_workspace.id)
        db.add(foreign_org)
        db.flush()

        # OWN tenant.
        own_workspace = TenantWorkspace(name="Own Co", slug="own-co")
        db.add(own_workspace)
        db.flush()
        own_org = Organization(name="Own Org", tenant_workspace_id=own_workspace.id)
        db.add(own_org)
        db.flush()

        # Synthetic actor: workspace admin of OWN workspace, no is_active field.
        actor = User(
            name="Actor Admin",
            email="actor-admin@example.test",
            is_admin=False,
        )
        db.add(actor)
        db.flush()
        db.add(TenantWorkspaceMember(user_id=actor.id, workspace_id=own_workspace.id, role="admin"))
        db.commit()

        actor_id = actor.id
        own_org_id = own_org.id
        foreign_org_id = foreign_org.id
        own_workspace_id = own_workspace.id
        foreign_workspace_id = foreign_workspace.id
    finally:
        db.close()

    def _override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    def _override_require_user():
        session = TestingSessionLocal()
        try:
            return session.get(User, actor_id)
        finally:
            session.close()

    app = FastAPI()
    app.include_router(projects_api.router)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_user] = _override_require_user

    client = TestClient(app)

    def _counts():
        session = TestingSessionLocal()
        try:
            project_count = session.scalar(select(func.count(Project.id))) or 0
            member_count = session.scalar(select(func.count(ProjectMember.id))) or 0
            return project_count, member_count
        finally:
            session.close()

    def _projects_for_org(org_id: int):
        session = TestingSessionLocal()
        try:
            return list(session.scalars(select(Project).where(Project.organization_id == org_id)))
        finally:
            session.close()

    def _members_for_actor():
        session = TestingSessionLocal()
        try:
            return list(session.scalars(select(ProjectMember).where(ProjectMember.user_id == actor_id)))
        finally:
            session.close()

    yield {
        "client": client,
        "session_factory": TestingSessionLocal,
        "actor_id": actor_id,
        "own_org_id": own_org_id,
        "foreign_org_id": foreign_org_id,
        "own_workspace_id": own_workspace_id,
        "foreign_workspace_id": foreign_workspace_id,
        "counts": _counts,
        "projects_for_org": _projects_for_org,
        "members_for_actor": _members_for_actor,
    }

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_http_create_project_foreign_org_denied_no_side_effects(http_env):
    client = http_env["client"]
    foreign_org_id = http_env["foreign_org_id"]

    before_projects, before_members = http_env["counts"]()

    response = client.post(
        "/projects/",
        json={"name": "Foreign Attempt", "organization_id": foreign_org_id},
    )

    assert response.status_code in (403, 404), response.text

    after_projects, after_members = http_env["counts"]()
    assert after_projects == before_projects
    assert after_members == before_members
    assert http_env["projects_for_org"](foreign_org_id) == []
    assert http_env["members_for_actor"]() == []


def test_http_create_project_own_org_succeeds_with_owner_membership(http_env):
    client = http_env["client"]
    own_org_id = http_env["own_org_id"]
    actor_id = http_env["actor_id"]

    response = client.post(
        "/projects/",
        json={"name": "Own Project", "organization_id": own_org_id},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organization_id"] == own_org_id
    assert body["name"] == "Own Project"

    created = http_env["projects_for_org"](own_org_id)
    assert len(created) == 1
    project_id = created[0].id

    members = http_env["members_for_actor"]()
    assert len(members) == 1
    assert members[0].project_id == project_id
    assert members[0].user_id == actor_id
    assert members[0].role == "owner"


def test_http_create_project_omitted_org_never_selects_foreign(http_env):
    client = http_env["client"]
    own_org_id = http_env["own_org_id"]
    foreign_org_id = http_env["foreign_org_id"]

    before_projects, before_members = http_env["counts"]()

    response = client.post("/projects/", json={"name": "Default Org Attempt"})

    if response.status_code == 200:
        body = response.json()
        # Success is only acceptable if it selected the actor's OWN organization.
        assert body["organization_id"] == own_org_id, response.text
        created = http_env["projects_for_org"](own_org_id)
        assert len(created) == 1
        members = http_env["members_for_actor"]()
        assert len(members) == 1
        assert members[0].role == "owner"
    else:
        # Safe denial is acceptable.
        assert response.status_code in (403, 404, 422), response.text
        after_projects, after_members = http_env["counts"]()
        assert after_projects == before_projects
        assert after_members == before_members

    # In every case, no project may be created under the foreign organization.
    assert http_env["projects_for_org"](foreign_org_id) == []
