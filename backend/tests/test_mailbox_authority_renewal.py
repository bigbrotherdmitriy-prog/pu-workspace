from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from fastapi import HTTPException, Response

from app.api.integrations import renew_mailbox_authority
from app.mailbox_identity.authority import renew_project_mailbox_authority
from app.mailbox_identity.dto import MailboxAuthorityRenewal
from app.mailbox_identity.runtime import require_mailbox_authority
from app.mailbox_identity.service import MailboxConflict, MailboxIdentityService
from app.models.audit_log import AuditLog
from app.models.project_member import ProjectMember
from test_v54_mailbox_rollout_controls import rollout_world, apply


@pytest.fixture
def world(db_session, user_factory):
    w = rollout_world(db_session, user_factory)
    w.now = datetime.now(timezone.utc)
    w.authority.valid_until = w.now - timedelta(days=1)
    w.authority.permissions = ["ingest", "read", "rollout"]
    w.db.commit()
    return w


def command(w, **changes):
    values = dict(organization_id=w.organization.id, project_id=w.project.id,
                  mail_connection_id=w.connection.id, credential_generation=w.generation,
                  binding_epoch=w.identity.binding_epoch,
                  valid_until=w.now + timedelta(hours=12), approval="CONFIRM",
                  reason="owner_confirmed_project_reconnect")
    values.update(changes)
    return MailboxAuthorityRenewal(**values)


def renew(w, **changes):
    return renew_project_mailbox_authority(w.db, command(w, **changes), actor=w.actor,
                                          expected_version=7, now=w.now)


def runtime(w):
    return SimpleNamespace(organization_id=w.organization.id,
                           mail_connection_id=w.connection.id, generation=w.generation)


def test_expired_active_renewal_is_scoped_audited_and_does_not_enable_flags(world):
    w = world
    result = renew(w)
    assert result["authority_version"] == 8
    assert w.authority.scope_project_id == w.project.id
    assert w.authority.scope_credential_generation == w.generation
    assert w.authority.permissions == ["ingest", "read", "rollout"]
    assert not any(getattr(w.flags, k) for k in (
        "shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions"))
    audit = w.db.scalar(select(AuditLog).where(AuditLog.action == "mailbox_authority_renewed"))
    assert f"actor_user_id={w.actor.id}" in audit.details
    assert "owner_confirmed_project_reconnect" in audit.details
    assert "private" not in audit.details
    require_mailbox_authority(w.db, runtime=runtime(w), actor=w.actor,
                              permission="ingest", project_id=w.project.id, expected_version=8)


@pytest.mark.parametrize("permission,project_offset,generation_offset", [
    ("ingest", 1, 0), ("read", 1, 0), ("rollout", 1, 0),
    ("action", 0, 0), ("reconcile", 0, 0), ("ingest", 0, 1),
])
def test_renewed_grant_cannot_escape_scope(world, permission, project_offset, generation_offset):
    w = world
    renew(w)
    r = runtime(w)
    r.generation += generation_offset
    with pytest.raises(ValueError):
        require_mailbox_authority(w.db, runtime=r, actor=w.actor,
                                  permission=permission, project_id=w.project.id + project_offset)


def test_scope_required_and_rotation_invalidates_old_grant(world):
    w = world
    renew(w)
    with pytest.raises(ValueError):
        require_mailbox_authority(w.db, runtime=runtime(w), actor=w.actor, permission="ingest")
    w.identity.credential_generation += 1
    w.db.flush()
    with pytest.raises(ValueError):
        require_mailbox_authority(w.db, runtime=runtime(w), actor=w.actor,
                                  permission="ingest", project_id=w.project.id)


def test_owner_loss_and_expiry_deny_use_of_renewed_grant(world):
    w = world
    renew(w)
    member = w.db.scalar(select(ProjectMember).where(ProjectMember.project_id == w.project.id))
    member.role = "manager"
    w.db.flush()
    with pytest.raises(ValueError):
        require_mailbox_authority(w.db, runtime=runtime(w), actor=w.actor,
                                  permission="ingest", project_id=w.project.id)
    member.role = "owner"
    w.authority.valid_until = w.now - timedelta(seconds=1)
    w.db.flush()
    with pytest.raises(ValueError):
        require_mailbox_authority(w.db, runtime=runtime(w), actor=w.actor,
                                  permission="ingest", project_id=w.project.id)


@pytest.mark.parametrize("field", ["project_id", "organization_id", "credential_generation", "binding_epoch"])
def test_wrong_scope_pins_cannot_renew(world, field):
    original = getattr(command(world), field)
    with pytest.raises(MailboxConflict):
        renew(world, **{field: original + 1})


def test_allowed_rollout_and_cohort_only_then_forbidden_primary_and_actions(world):
    w = world
    renew(w)
    for flag in ("shadow_write", "shadow_read_compare", "pilot_write"):
        apply(w, flag)
    from app.models.mailbox_identity import MailboxProjectCohort
    cohort = w.db.scalar(select(MailboxProjectCohort))
    MailboxIdentityService().change_project_cohort(w.db,
        organization_id=w.organization.id, project_id=w.project.id,
        mail_connection_id=w.connection.id, credential_generation=w.generation,
        binding_epoch=w.identity.binding_epoch, enabled=True, actor=w.actor,
        authority_version=8, expected_record_version=cohort.record_version)
    assert cohort.enabled
    for flag in ("primary_read", "actions"):
        with pytest.raises(MailboxConflict):
            apply(w, flag)
    assert not w.flags.primary_read and not w.flags.actions


@pytest.mark.parametrize("change", [
    "revoked", "missing", "manager", "admin_not_owner", "foreign_scope",
    "stale_identity", "extra_permission", "corrupt_scope", "token_other_project",
])
def test_renewal_rejects_non_owner_or_expansion(world, change):
    w = world
    if change == "revoked": w.authority.state = "revoked"
    if change == "missing": w.db.delete(w.authority)
    if change in {"manager", "admin_not_owner"}:
        member = w.db.scalar(select(ProjectMember).where(ProjectMember.project_id == w.project.id))
        member.role = "manager"
        w.actor.is_admin = change == "admin_not_owner"
    if change == "foreign_scope":
        w.authority.scope_project_id = w.project.id + 1
        w.authority.scope_credential_generation = w.generation
    if change == "stale_identity": w.identity.credential_generation += 1
    if change == "extra_permission": w.authority.permissions = ["rollout", "action"]
    if change == "corrupt_scope": w.authority.scope_project_id = w.project.id
    if change == "token_other_project":
        from app.models.project import Project
        other = Project(name="Other", organization_id=w.organization.id)
        w.db.add(other); w.db.flush(); w.token.project_id = other.id
    w.db.flush()
    with pytest.raises(MailboxConflict): renew(w)
    assert not w.db.scalar(select(AuditLog).where(AuditLog.action == "mailbox_authority_renewed"))


@pytest.mark.parametrize("hours", [-1, 0, 25, 120])
def test_ttl_bounded(world, hours):
    with pytest.raises(MailboxConflict):
        renew(world, valid_until=world.now + timedelta(hours=hours))


def test_replay_stale_cas_and_rollback(world):
    w = world
    renew(w)
    with pytest.raises(MailboxConflict): renew(w)
    w.db.rollback()
    assert w.authority.authority_version == 7
    assert w.authority.scope_project_id is None
    assert not w.db.scalar(select(AuditLog).where(AuditLog.action == "mailbox_authority_renewed"))


def test_http_etag_and_stale_cas(world):
    w = world
    response = Response()
    result = renew_mailbox_authority(command(w), response, '"7"', w.db, w.actor)
    assert result["authority_version"] == 8 and response.headers["etag"] == '"8"'
    with pytest.raises(HTTPException) as exc:
        renew_mailbox_authority(command(w), Response(), '"7"', w.db, w.actor)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("changes", [{"approval": "AUTO"}, {"permissions": ["action"]},
    {"valid_until": "2026-10-01T00:00:00"}, {"project_id": True}])
def test_dto_rejects_implicit_approval_permissions_and_naive_time(world, changes):
    with pytest.raises(ValidationError): command(world, **changes)
