"""Pending-row scope checks use a real Session, not a PostgreSQL substitute."""
import pytest
from sqlalchemy import event, select

from app.core.v54_authority import AuthorityDenied, AuthorityResolver
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState
from test_v54_authority import sessions  # noqa: F401
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import NOW


@pytest.mark.parametrize("lock", [False, True])
@pytest.mark.parametrize("kind", ["project", "user", "member", "mandate"])
def test_provably_unrelated_pending_security_row_is_not_flushed(sessions, lock, kind):
    with sessions() as db:
        rows = {
            "project": Project(id=999, organization_id=1, name="unrelated"),
            "user": User(id=999, email="synthetic@example.invalid"),
            "member": ProjectMember(project_id=999, user_id=2, role="owner"),
            "mandate": AuthorityState(project_id=999, organization_id=1,
                principal_kind="user", principal_id="2", scope="v54.synthetic.confirm"),
        }
        pending = rows[kind]
        db.add(pending)
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            snapshot = AuthorityResolver().require(db, scope(), "metadata", NOW, lock=lock)
            assert snapshot.membership_role == "owner"
            assert set(db.new) == {pending} and not db.dirty and not db.deleted
            assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)


@pytest.mark.parametrize("change", ["project_id", "user_id", "member_project", "member_user",
                                    "mandate_project", "mandate_actor", "mandate_tenant", "mandate_scope"])
def test_moving_security_row_out_of_scope_does_not_evade_pending_guard(sessions, change):
    with sessions() as db:
        if change == "project_id":
            row, field, value = db.get(Project, 4), "id", 999
        elif change == "user_id":
            row, field, value = db.get(User, 2), "id", 999
        elif change.startswith("member"):
            row = db.scalar(select(ProjectMember).where(ProjectMember.project_id == 4, ProjectMember.user_id == 2))
            field, value = ("project_id", 999) if change == "member_project" else ("user_id", 999)
        else:
            row = db.scalar(select(AuthorityState).where(AuthorityState.principal_kind == "user", AuthorityState.principal_id == "2"))
            field, value = {"mandate_project": ("project_id", 999), "mandate_actor": ("principal_id", "999"),
                "mandate_tenant": ("organization_id", 999), "mandate_scope": ("scope", "unrelated")}[change]
        setattr(row, field, value)
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            with pytest.raises(AuthorityDenied, match="^resource_unavailable$"):
                AuthorityResolver().require(db, scope(), "metadata", NOW)
            assert not statements and row in db.dirty and getattr(row, field) == value
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)


def test_unknown_previous_scope_denies_without_loading_it(sessions):
    with sessions() as db:
        row = db.scalar(select(ProjectMember).where(ProjectMember.project_id == 4, ProjectMember.user_id == 2))
        db.expire(row, ["project_id"])
        row.project_id = 999
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            with pytest.raises(AuthorityDenied, match="^resource_unavailable$"):
                AuthorityResolver().require(db, scope(), "metadata", NOW)
            assert not statements and row in db.dirty and row.project_id == 999
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)


@pytest.mark.parametrize("change", ["insert", "delete", "role", "move"])
def test_task_assignee_pending_membership_never_flushes_or_authorizes(sessions, change):
    with sessions() as db:
        member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == 4, ProjectMember.user_id == 3))
        if change == "insert":
            # Add a pending duplicate: it must be denied before uniqueness/DML.
            member = ProjectMember(project_id=4, user_id=3, role="manager")
            db.add(member)
        elif change == "delete":
            db.delete(member)
        elif change == "role":
            member.role = "viewer"
        else:
            member.user_id = 999
        before = (set(db.new), set(db.dirty), set(db.deleted))
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            with pytest.raises(AuthorityDenied, match="^resource_unavailable$"):
                AuthorityResolver(clock=lambda: NOW).authorize_subject(db, scope(), "task.assign", scope(3).actor)
            assert (set(db.new), set(db.dirty), set(db.deleted)) == before
            assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)


def test_task_assignee_query_does_not_flush_other_pending_rows(sessions):
    with sessions() as db:
        row = Project(id=999, organization_id=1, name="unrelated")
        db.add(row)
        assert AuthorityResolver(clock=lambda: NOW).authorize_subject(db, scope(), "task.assign", scope(3).actor)
        assert row in db.new and not db.dirty and not db.deleted
