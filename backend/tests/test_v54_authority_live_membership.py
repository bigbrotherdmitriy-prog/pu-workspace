"""Real Session identity-map regressions; no PostgreSQL concurrency claim."""
from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from app.core.v54_authority import AuthorityDenied, AuthorityResolver
from app.models.project import Project
from app.models.audit_log import AuditLog
from app.models.project_member import ProjectMember
from app.models.v54_authority import AuthorityState
from test_v54_authority import sessions  # noqa: F401
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import NOW


@pytest.mark.parametrize("lock", [False, True])
def test_live_membership_change_in_other_session_overrides_cached_owner(sessions, lock):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions() as reader:
        member = reader.scalar(select(ProjectMember).where(
            ProjectMember.project_id == 4, ProjectMember.user_id == 2))
        assert member.role == "owner"
        reader.commit()  # expire_on_commit=False deliberately preserves identity map.
        with sessions.begin() as writer:
            live = writer.get(ProjectMember, member.id)
            live.role = "viewer"
        reader.begin()
        assert reader.scalar(select(ProjectMember.role).where(ProjectMember.id == member.id)) == "viewer"
        assert member.role == "owner"
        with pytest.raises(AuthorityDenied, match="^resource_unavailable$"):
            resolver.require(reader, scope(), "metadata", NOW, lock=lock)
        assert member.role == "viewer"


@pytest.mark.parametrize("lock", [False, True])
@pytest.mark.parametrize("outer_no_autoflush", [False, True])
@pytest.mark.parametrize("change", ["role", "mandate", "archive", "delete_membership"])
def test_pending_security_changes_are_denied_without_flush_or_refresh(
    sessions, lock, outer_no_autoflush, change,
):
    resolver = AuthorityResolver(clock=lambda: NOW)
    with sessions() as db:
        member = db.scalar(select(ProjectMember).where(ProjectMember.user_id == 2, ProjectMember.project_id == 4))
        mandate = db.scalar(select(AuthorityState).where(
            AuthorityState.principal_kind == "user", AuthorityState.principal_id == "2"))
        project = db.get(Project, 4)
        if change == "role":
            member.role = "viewer"
        elif change == "mandate":
            mandate.state = "revoked"
            mandate.authority_epoch += 1
            mandate.record_version += 1
            mandate.updated_at = NOW + timedelta(seconds=1)
        elif change == "archive":
            project.archived_at = NOW
        else:
            db.delete(member)
        before_dirty, before_deleted = set(db.dirty), set(db.deleted)
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            with db.no_autoflush if outer_no_autoflush else nullcontext():
                with pytest.raises(AuthorityDenied, match="^resource_unavailable$"):
                    resolver.require(db, scope(), "metadata", NOW, lock=lock)
            assert not statements, "guard must precede every refresh/autoflush/query"
            assert set(db.dirty) == before_dirty and set(db.deleted) == before_deleted
            if change == "role": assert member.role == "viewer"
            if change == "mandate": assert mandate.state == "revoked"
            if change == "archive": assert project.archived_at == NOW
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)
            db.rollback()


@pytest.mark.parametrize("lock", [False, True])
def test_clean_same_role_membership_still_requires_explicit_mandate(sessions, lock):
    with sessions.begin() as db:
        snapshot = AuthorityResolver(clock=lambda: NOW).require(db, scope(), "metadata", NOW, lock=lock)
        assert snapshot.membership_role == "owner" and snapshot.authority_epoch == 1
        with pytest.raises(AuthorityDenied):
            AuthorityResolver(clock=lambda: NOW).require(db, scope(4), "metadata", NOW, lock=lock)


def test_authorization_does_not_flush_unrelated_pending_business_rows(sessions):
    with sessions() as db:
        pending = AuditLog(action="synthetic.pending", entity_type="synthetic")
        db.add(pending)
        snapshot = AuthorityResolver(clock=lambda: NOW).require(db, scope(), "metadata", NOW)
        assert snapshot.membership_role == "owner"
        assert pending in db.new and pending.id is None
        db.rollback()


def test_explicit_caller_flush_preserves_supported_mandate_changes(sessions):
    with sessions.begin() as db:
        mandate = db.scalar(select(AuthorityState).where(
            AuthorityState.principal_kind == "user", AuthorityState.principal_id == "2"))
        mandate.permissions = [*mandate.permissions, "fragment"]
        mandate.authority_epoch += 1
        mandate.record_version += 1
        mandate.updated_at = NOW + timedelta(seconds=1)
        db.flush()
        snapshot = AuthorityResolver(clock=lambda: NOW).require(db, scope(), "fragment", NOW)
        assert snapshot.authority_epoch == 2
