from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from fastapi import Response

from app.api.integrations import _if_match_version, change_mailbox_rollout, router
from app.mailbox_identity.dto import MailboxRolloutTransition
from app.mailbox_identity.runtime import runtime_for_project_connection
from app.mailbox_identity.service import MailboxConflict, MailboxIdentityService
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import MailboxAuthorityState, MailboxCutoverFlags
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.user import User


NOW = datetime.now(timezone.utc)


def rollout_world(db_session, user_factory, *, admin=False):
    db = db_session
    actor = user_factory(is_admin=admin, email="operator-private@example.test")
    organization = Organization(name="Private rollout organization")
    db.add(organization)
    db.flush()
    project = Project(name="Mailbox rollout", organization_id=organization.id)
    db.add(project)
    db.flush()
    token = GoogleOAuthToken(
        project_id=project.id,
        access_token="private-access-token",
        refresh_token="private-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
    )
    db.add(token)
    db.flush()
    identity, connection, generation = MailboxIdentityService().bind_verified_google_subject(
        db,
        organization_id=organization.id,
        google_token_id=token.id,
        subject="private-google-subject",
        now=NOW,
    )
    authority = MailboxAuthorityState(
        organization_id=organization.id,
        mail_connection_id=connection.id,
        principal_kind="user",
        principal_id=str(actor.id),
        permissions=["rollout"],
        state="active",
        authority_version=7,
        valid_until=NOW + timedelta(days=1),
    )
    db.add(authority)
    db.flush()
    flags = db.scalar(select(MailboxCutoverFlags))
    return SimpleNamespace(
        db=db,
        actor=actor,
        organization=organization,
        project=project,
        token=token,
        identity=identity,
        connection=connection,
        generation=generation,
        authority=authority,
        flags=flags,
    )


def transition(world, target_flag, enabled=True, **changes):
    values = {
        "organization_id": world.organization.id,
        "mail_connection_id": world.connection.id,
        "credential_generation": world.generation,
        "binding_epoch": world.identity.binding_epoch,
        "authority_version": world.authority.authority_version,
        "flag": target_flag,
        "enabled": enabled,
        "approval": "CONFIRM",
    }
    values.update(changes)
    return MailboxRolloutTransition(**values)


def apply(world, flag, enabled=True, **changes):
    command = transition(world, flag, enabled, **changes)
    return MailboxIdentityService().change_rollout_flags(
        world.db,
        command,
        actor=world.actor,
        expected_record_version=world.flags.record_version,
    )


def audit_rows(world):
    return list(world.db.scalars(select(AuditLog).where(
        AuditLog.action == "mailbox_rollout_transition_confirmed"
    )))


def test_route_and_strict_if_match_contract():
    assert "/integrations/mailbox-rollout" in {route.path for route in router.routes}
    assert _if_match_version('"7"') == 7
    for invalid in ("7", 'W/"7"', "*", '"0"', '"01"', '"private-google-subject"'):
        with pytest.raises(ValueError, match="resource_unavailable"):
            _if_match_version(invalid)


def test_api_commits_one_transition_and_returns_next_etag(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    response = Response()
    result = change_mailbox_rollout(
        transition(world, "shadow_write"), response, '"1"', world.db, world.actor
    )
    assert result.record_version == 2
    assert response.headers["etag"] == '"2"'
    assert len(audit_rows(world)) == 1


def test_dto_requires_exact_pins_and_confirm_never_auto(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    for change in (
        {"approval": "AUTO"},
        {"organization_id": True},
        {"credential_generation": 0},
        {"binding_epoch": 0},
        {"authority_version": 0},
        {"mail_connection_id": "not-a-uuid"},
        {"flag": "all"},
    ):
        with pytest.raises(ValidationError):
            values = transition(world, "shadow_write").model_dump()
            values.update(change)
            MailboxRolloutTransition(**values)


def test_monotonic_enablement_is_one_confirmed_audited_transition_each(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    stages = ("shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions")
    for expected_version, flag in enumerate(stages, start=2):
        result = apply(world, flag)
        assert result.flag == flag
        assert result.enabled is True
        assert result.record_version == expected_version
        assert world.flags.record_version == expected_version
    assert all(getattr(world.flags, flag) for flag in stages)
    audits = audit_rows(world)
    assert len(audits) == len(stages)
    assert [row.details for row in audits] == [
        f"flag={flag};enabled=true;from_version={index};to_version={index + 1};actor_user_id={world.actor.id}"
        for index, flag in enumerate(stages, start=1)
    ]


def test_prerequisites_and_noop_fail_without_write_or_audit(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    for flag in ("pilot_write", "primary_read", "actions"):
        with pytest.raises(MailboxConflict, match="resource_unavailable"):
            apply(world, flag)
    assert world.flags.record_version == 1
    assert not audit_rows(world)
    apply(world, "shadow_write")
    before_version = world.flags.record_version
    before_audits = len(audit_rows(world))
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        apply(world, "shadow_write")
    assert world.flags.record_version == before_version
    assert len(audit_rows(world)) == before_audits


def test_rollback_only_moves_one_step_to_a_safer_valid_state(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    for flag in ("shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions"):
        apply(world, flag)
    for unsafe in ("primary_read", "pilot_write", "shadow_write", "shadow_read_compare"):
        with pytest.raises(MailboxConflict, match="resource_unavailable"):
            apply(world, unsafe, False)
    for flag in ("actions", "primary_read", "pilot_write", "shadow_read_compare", "shadow_write"):
        apply(world, flag, False)
    assert not any((world.flags.shadow_write, world.flags.shadow_read_compare,
                    world.flags.pilot_write, world.flags.primary_read, world.flags.actions))
    assert len(audit_rows(world)) == 10


def test_stale_if_match_and_wrong_scope_pins_are_fail_closed(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    service = MailboxIdentityService()
    command = transition(world, "shadow_write")
    with pytest.raises(MailboxConflict, match="flags_version_conflict"):
        service.change_rollout_flags(world.db, command, actor=world.actor, expected_record_version=2)
    for changed in (
        {"organization_id": world.organization.id + 1},
        {"mail_connection_id": "00000000-0000-0000-0000-000000000000"},
        {"credential_generation": world.generation + 1},
        {"binding_epoch": world.identity.binding_epoch + 1},
    ):
        with pytest.raises(MailboxConflict, match="resource_unavailable"):
            service.change_rollout_flags(
                world.db, transition(world, "shadow_write", **changed), actor=world.actor,
                expected_record_version=1,
            )
    assert world.flags.record_version == 1
    assert not audit_rows(world)


def test_human_mailbox_authority_is_required_even_for_global_admin(db_session, user_factory):
    world = rollout_world(db_session, user_factory, admin=True)
    service = MailboxIdentityService()
    world.db.delete(world.authority)
    world.db.flush()
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        service.change_rollout_flags(
            world.db, transition(world, "shadow_write"), actor=world.actor,
            expected_record_version=1,
        )
    world.db.add(MailboxAuthorityState(
        organization_id=world.organization.id,
        mail_connection_id=world.connection.id,
        principal_kind="service",
        principal_id=str(world.actor.id),
        permissions=["rollout"],
        state="active",
        authority_version=7,
        valid_until=NOW + timedelta(days=1),
    ))
    world.db.flush()
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        service.change_rollout_flags(
            world.db, transition(world, "shadow_write"), actor=world.actor,
            expected_record_version=1,
        )


@pytest.mark.parametrize("mode", ("revoked", "expired", "stale"))
def test_revoked_expired_or_stale_authority_cannot_change_flags(
    db_session, user_factory, mode
):
    world = rollout_world(db_session, user_factory)
    command = transition(world, "shadow_write")
    if mode == "revoked":
        world.authority.state = "revoked"
    elif mode == "expired":
        world.authority.valid_until = NOW - timedelta(seconds=1)
    else:
        world.authority.authority_version += 1
    world.db.flush()
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        MailboxIdentityService().change_rollout_flags(
            world.db, command, actor=world.actor, expected_record_version=1
        )


def test_rotation_and_revoke_close_old_generation_controls(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    apply(world, "shadow_write")
    old_generation = world.generation
    _identity, _mail, new_generation = MailboxIdentityService().bind_verified_google_subject(
        world.db,
        organization_id=world.organization.id,
        google_token_id=world.token.id,
        subject="private-google-subject",
        now=NOW,
    )
    assert new_generation == old_generation + 1
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        MailboxIdentityService().change_rollout_flags(
            world.db, transition(world, "shadow_read_compare"), actor=world.actor,
            expected_record_version=world.flags.record_version,
        )
    current = MailboxIdentityService().flags(
        world.db,
        organization_id=world.organization.id,
        mail_connection_id=world.connection.id,
        generation=new_generation,
    )
    assert current.record_version == 1
    assert not any((current.shadow_write, current.shadow_read_compare, current.pilot_write,
                    current.primary_read, current.actions))
    world.identity.state = "revoked"
    world.db.flush()
    with pytest.raises(MailboxConflict, match="resource_unavailable"):
        MailboxIdentityService().change_rollout_flags(
            world.db,
            transition(world, "shadow_write", credential_generation=new_generation),
            actor=world.actor,
            expected_record_version=1,
        )


def test_audit_contains_no_mailbox_provider_or_credential_secrets(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    apply(world, "shadow_write")
    serialized = "\n".join(
        f"{row.action}|{row.entity_type}|{row.entity_id}|{row.details}" for row in audit_rows(world)
    )
    for sensitive in (
        world.actor.email,
        world.identity.account_key,
        world.connection.id,
        world.token.access_token,
        world.token.refresh_token,
    ):
        assert sensitive not in serialized


def test_runtime_rejects_out_of_lattice_flags_even_if_storage_is_tampered(db_session, user_factory):
    world = rollout_world(db_session, user_factory)
    world.flags.actions = True
    world.db.flush()
    with pytest.raises(ValueError, match="resource_unavailable"):
        runtime_for_project_connection(world.db, world.project.id)
