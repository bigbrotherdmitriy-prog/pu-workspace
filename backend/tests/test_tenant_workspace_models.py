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
