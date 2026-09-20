from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.project_contacts import (
    ContactResolutionCommand,
    ContactUpdate,
    contact_for_sender,
    discover_contact_from_message,
    normalize_email,
    normalize_phone,
    resolve_contact,
    update_contact,
)
from app.models.management import ManagementHistory
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_contact import ProjectContact
from app.models.project_member import ProjectMember
from app.models.v54_pilot import ConnectionIdentity, MailConnection


def _world(db, user_factory):
    user = user_factory()
    organization = Organization(name="Contact hardening tenant")
    db.add(organization); db.flush()
    projects = [Project(name=f"Contact project {index}", organization_id=organization.id) for index in (1, 2)]
    db.add_all(projects); db.flush()
    db.add_all(ProjectMember(project_id=project.id, user_id=user.id, role="manager") for project in projects)
    mailboxes = []
    for index in (1, 2):
        identity = ConnectionIdentity(
            id=str(uuid4()), organization_id=organization.id, provider="google",
            account_key=f"contact-account-{index}", state="verified", binding_epoch=1, record_version=1,
        )
        db.add(identity); db.flush()
        mailbox = MailConnection(
            id=str(uuid4()), organization_id=organization.id, identity_id=identity.id,
            namespace=f"contact-mailbox-{index}", state="active", record_version=1,
        )
        db.add(mailbox); mailboxes.append(mailbox)
    db.commit()
    return user, organization, projects, mailboxes


def _command(key: str, **overrides):
    values = dict(
        decision_key=key, expected_record_version=1,
        decision="confirm", reason_code="reviewed_by_operator",
    )
    values.update(overrides)
    return ContactResolutionCommand(**values)


def test_email_domain_and_phone_normalization_are_deterministic():
    assert normalize_email(" Клиент <SALES@пример.рф> ") == "sales@xn--e1afmkfd.xn--p1ai"
    assert normalize_phone("8 (999) 123-45-67") == "+79991234567"
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"
    with pytest.raises(HTTPException):
        normalize_phone("123")


def test_same_sender_is_scoped_to_exact_verified_mailbox(db_session, user_factory):
    user, _, projects, mailboxes = _world(db_session, user_factory)
    first = discover_contact_from_message(
        db_session, projects[0].id, "Sales <SALES@пример.рф>", "First", user,
        mail_connection_id=mailboxes[0].id,
    )
    second = discover_contact_from_message(
        db_session, projects[1].id, "sales@xn--e1afmkfd.xn--p1ai", "Second", user,
        mail_connection_id=mailboxes[1].id,
    )
    assert first.id != second.id
    assert first.normalized_domain == "xn--e1afmkfd.xn--p1ai"
    for contact in (first, second):
        resolve_contact(contact.id, _command(f"mailbox-confirm-{contact.id}"), db_session, user)
    assert contact_for_sender(
        db_session, projects[0].id, "sales@пример.рф", user,
        mail_connection_id=mailboxes[0].id,
    ).id == first.id
    assert contact_for_sender(
        db_session, projects[1].id, "sales@пример.рф", user,
        mail_connection_id=mailboxes[1].id,
    ).id == second.id
    assert contact_for_sender(db_session, projects[0].id, "sales@пример.рф", user) is None


def test_revoked_or_cross_tenant_mailbox_fails_closed(db_session, user_factory):
    user, _, projects, mailboxes = _world(db_session, user_factory)
    mailboxes[0].state = "revoked"; db_session.commit()
    assert discover_contact_from_message(
        db_session, projects[0].id, "client@example.test", "Synthetic", user,
        mail_connection_id=mailboxes[0].id,
    ) is None
    assert contact_for_sender(
        db_session, projects[0].id, "client@example.test", user,
        mail_connection_id=mailboxes[1].id,
    ) is None


def test_resolution_is_replay_safe_and_history_contains_no_raw_pii(db_session, user_factory):
    user, _, projects, mailboxes = _world(db_session, user_factory)
    contact = discover_contact_from_message(
        db_session, projects[0].id, "Person <person@example.test>", "Synthetic", user,
        mail_connection_id=mailboxes[0].id,
    )
    command = _command(
        "contact-decision-0001", decision="correct",
        email="Corrected@пример.рф", phone="8 999 111 22 33",
    )
    first = resolve_contact(contact.id, command, db_session, user)
    replay = resolve_contact(contact.id, command, db_session, user)
    assert first["record_version"] == 2
    assert replay["already_applied"] is True
    history = db_session.scalar(select(ManagementHistory).where(
        ManagementHistory.entity_type == "project_contact",
        ManagementHistory.idempotency_key == command.decision_key,
    ))
    serialized = str({
        "old": history.old_values, "new": history.new_values,
        "evidence": history.evidence, "reason": history.reason,
    })
    assert "person@example.test" not in serialized
    assert "corrected@" not in serialized.casefold()
    assert "79991112233" not in serialized
    assert history.command_hash and history.new_values["email_hash"]


def test_decision_key_collision_and_legacy_confirmation_bypass_fail_closed(db_session, user_factory):
    user, _, projects, mailboxes = _world(db_session, user_factory)
    contacts = [discover_contact_from_message(
        db_session, projects[0].id, f"person-{index}@example.test", "Synthetic", user,
        mail_connection_id=mailboxes[0].id,
    ) for index in (1, 2)]
    resolve_contact(contacts[0].id, _command("contact-decision-collision"), db_session, user)
    with pytest.raises(HTTPException) as collision:
        resolve_contact(contacts[1].id, _command("contact-decision-collision"), db_session, user)
    assert collision.value.status_code == 409
    with pytest.raises(HTTPException) as bypass:
        update_contact(
            contacts[1].id, ContactUpdate(expected_record_version=1, confirmed=True),
            db_session, user,
        )
    assert bypass.value.status_code == 409
