from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from fastapi import HTTPException, Response

from app.api.integrations import join_mailbox_rollout, renew_mailbox_authority
from app.mailbox_identity.authority import renew_project_mailbox_authority
from app.mailbox_identity.dto import MailboxAuthorityRenewal, MailboxCohortJoin
from app.mailbox_identity.runtime import require_mailbox_authority
from app.mailbox_identity.service import MailboxConflict, MailboxIdentityService
from app.models.audit_log import AuditLog
from app.models.mailbox_identity import MailboxProjectCohort
from app.models.organization_contract import Organization
from app.models.project import Project
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


def add_sibling_project(w, *, owner=True, organization=None):
    """Add another project to the world, optionally owned by the same actor.

    Used to exercise the Variant 1 (ADR-V6-08) org-scoped mandate: a renewal
    for one project must work for any other project the same owner holds in
    the same organization, and must not work outside of it.
    """
    org = organization or w.organization
    project = Project(name="Sibling project", organization_id=org.id)
    w.db.add(project)
    w.db.flush()
    if owner:
        w.db.add(ProjectMember(project_id=project.id, user_id=w.actor.id, role="owner"))
        w.db.flush()
    return project


def test_expired_active_renewal_is_scoped_audited_and_does_not_enable_flags(world):
    w = world
    result = renew(w)
    assert result["authority_version"] == 8
    assert w.authority.scope_project_id is None
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


@pytest.mark.parametrize("permission,generation_offset", [
    ("ingest", 1), ("action", 0), ("reconcile", 0),
])
def test_renewed_grant_rejects_wrong_generation_and_unrelated_permissions(
    world, permission, generation_offset,
):
    """Generation-pinning and unknown permissions are unrelated to project scope
    and must stay denied under Variant 1 exactly as before."""
    w = world
    renew(w)
    r = runtime(w)
    r.generation += generation_offset
    with pytest.raises(ValueError):
        require_mailbox_authority(w.db, runtime=r, actor=w.actor,
                                  permission=permission, project_id=w.project.id)


def test_renewed_grant_is_org_scoped_not_project_scoped(world):
    """ADR-V6-08 Variant 1: renewal no longer narrows to the one project it was
    requested for. The same owner's mandate must work for any other project
    they own in the same organization, on the current generation, and must
    still be denied for a project they don't own or that lives in another org."""
    w = world
    renew(w)
    r = runtime(w)

    other_owned = add_sibling_project(w, owner=True)
    authority = require_mailbox_authority(w.db, runtime=r, actor=w.actor,
                                          permission="ingest", project_id=other_owned.id)
    assert authority.scope_project_id is None

    not_owned = add_sibling_project(w, owner=False)
    with pytest.raises(ValueError):
        require_mailbox_authority(w.db, runtime=r, actor=w.actor,
                                  permission="ingest", project_id=not_owned.id)

    other_org = Organization(name="Other organization")
    w.db.add(other_org)
    w.db.flush()
    foreign_project = add_sibling_project(w, owner=True, organization=other_org)
    with pytest.raises(ValueError):
        require_mailbox_authority(w.db, runtime=r, actor=w.actor,
                                  permission="ingest", project_id=foreign_project.id)


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
    """ADR-V6-08 Variant 1 (Change 2) stops narrowing a renewed grant's
    scope_project_id to a single project (it's left NULL -- org-scoped), but
    the ADR is explicit that the primary_read/actions ban for any renewed
    grant is orthogonal and NOT touched by Variant 1 ("запрет primary_read/
    actions после сужения scope -- это ортогонально, остаётся как есть").

    A renewed grant always narrows scope_credential_generation instead (the
    real anti-replay signal), so change_rollout_flags keys the ban off that
    field being non-null rather than off scope_project_id. Reaching
    primary_read/actions for a project still requires an original
    bootstrap/change() grant, never a renewal -- cohort enrollment (join)
    only affects shadow/pilot-write reachability, not this ban.
    """
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


@pytest.mark.parametrize("change", [
    "revoked", "missing", "manager", "admin_not_owner", "foreign_scope",
    "stale_identity", "extra_permission",
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
    w.db.flush()
    with pytest.raises(MailboxConflict): renew(w)
    assert not w.db.scalar(select(AuditLog).where(AuditLog.action == "mailbox_authority_renewed"))


def test_renewal_succeeds_when_token_belongs_to_a_different_project_same_org(world):
    """ADR-V6-08 Variant 1 (Change 1): the generation's token no longer has to
    belong to the exact project being renewed -- only to the same organization,
    with owner-on-target-project enforced separately."""
    w = world
    other = Project(name="Other project, same org", organization_id=w.organization.id)
    w.db.add(other)
    w.db.flush()
    w.token.project_id = other.id
    w.db.flush()
    result = renew(w)
    assert result["authority_version"] == 8


def test_renewal_rejects_token_belonging_to_a_different_organization(world):
    w = world
    other_org = Organization(name="Foreign organization")
    w.db.add(other_org)
    w.db.flush()
    foreign_project = Project(name="Foreign project", organization_id=other_org.id)
    w.db.add(foreign_project)
    w.db.flush()
    w.token.project_id = foreign_project.id
    w.db.flush()
    with pytest.raises(MailboxConflict):
        renew(w)
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


def join(w, project, **changes):
    values = dict(organization_id=w.organization.id, project_id=project.id,
                  mail_connection_id=w.connection.id, credential_generation=w.generation,
                  binding_epoch=w.identity.binding_epoch, actor=w.actor,
                  authority_version=w.authority.authority_version)
    values.update(changes)
    return MailboxIdentityService().join_project_cohort(w.db, **values)


def join_audit_rows(w):
    return list(w.db.scalars(select(AuditLog).where(
        AuditLog.action == "mailbox_project_cohort_joined")))


def test_join_creates_and_enables_when_no_cohort_row_exists(world):
    w = world
    renew(w)
    other = add_sibling_project(w, owner=True)
    assert w.db.scalar(select(MailboxProjectCohort).where(
        MailboxProjectCohort.project_id == other.id)) is None
    cohort = join(w, other)
    assert cohort.enabled is True
    assert cohort.record_version == 1
    assert cohort.project_id == other.id
    assert cohort.changed_by_user_id == w.actor.id
    audits = join_audit_rows(w)
    assert len(audits) == 1
    assert audits[0].entity_id == cohort.id
    assert f"project_id={other.id}" in audits[0].details


def test_join_flips_disabled_cohort_to_enabled(world):
    w = world
    renew(w)
    existing = w.db.scalar(select(MailboxProjectCohort).where(
        MailboxProjectCohort.project_id == w.project.id,
        MailboxProjectCohort.credential_generation == w.generation))
    assert existing.enabled is False
    cohort = join(w, w.project)
    assert cohort.id == existing.id
    assert cohort.enabled is True
    assert cohort.record_version == 2
    audits = join_audit_rows(w)
    assert len(audits) == 1
    assert "from_version=1;to_version=2" in audits[0].details


def test_join_is_idempotent_when_already_enabled(world):
    w = world
    renew(w)
    first = join(w, w.project)
    assert first.enabled is True and first.record_version == 2
    second = join(w, w.project)
    assert second.id == first.id
    assert second.enabled is True
    assert second.record_version == 2
    assert len(join_audit_rows(w)) == 1


def test_join_rejects_non_owner_actor(world):
    w = world
    renew(w)
    other = add_sibling_project(w, owner=False)
    w.db.add(ProjectMember(project_id=other.id, user_id=w.actor.id, role="manager"))
    w.db.flush()
    with pytest.raises(MailboxConflict):
        join(w, other)
    assert not join_audit_rows(w)


def test_join_rejects_token_in_a_different_organization(world):
    w = world
    renew(w)
    other_org = Organization(name="Foreign organization for join")
    w.db.add(other_org)
    w.db.flush()
    foreign_project = Project(name="Foreign project for join", organization_id=other_org.id)
    w.db.add(foreign_project)
    w.db.flush()
    w.token.project_id = foreign_project.id
    w.db.flush()
    with pytest.raises(MailboxConflict):
        join(w, w.project)
    assert not join_audit_rows(w)


def test_join_rejects_stale_authority_version(world):
    w = world
    renew(w)
    with pytest.raises(MailboxConflict):
        join(w, w.project, authority_version=7)
    assert not join_audit_rows(w)


def test_http_join_etag_and_stale_cas(world):
    w = world
    renew(w)
    command = MailboxCohortJoin(
        organization_id=w.organization.id, project_id=w.project.id,
        mail_connection_id=w.connection.id, credential_generation=w.generation,
        binding_epoch=w.identity.binding_epoch,
    )
    response = Response()
    result = join_mailbox_rollout(command, response, '"8"', w.db, w.actor)
    assert result.enabled is True and result.record_version == 2
    assert response.headers["etag"] == '"2"'
    with pytest.raises(HTTPException) as exc:
        join_mailbox_rollout(command, Response(), '"7"', w.db, w.actor)
    assert exc.value.status_code == 409
