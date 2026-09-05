from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.project_contacts import (
    ContactResolutionCommand, ContactUpdate, contact_for_sender, discover_contact_from_message,
    normalize_email, normalize_phone, resolve_contact, update_contact,
)
from app.models.audit_log import AuditLog
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_contact import ProjectContact, ProjectContactHistory
from app.models.project_member import ProjectMember
from app.models.v54_pilot import ConnectionIdentity, MailConnection


@pytest.fixture
def world(db_session, user_factory):
    db = db_session
    reviewer = user_factory()
    outsider = user_factory()
    organization = Organization(name="Synthetic tenant")
    db.add(organization); db.flush()
    projects = [Project(name=f"Synthetic {number}", organization_id=organization.id) for number in (1, 2)]
    db.add_all(projects); db.flush()
    for project in projects:
        db.add(ProjectMember(project_id=project.id, user_id=reviewer.id, role="editor"))
    identity_ids = [str(uuid4()), str(uuid4())]
    mail_ids = [str(uuid4()), str(uuid4())]
    for index in range(2):
        db.add(ConnectionIdentity(
            id=identity_ids[index], organization_id=organization.id, provider="google",
            account_key=f"synthetic-{index}", state="verified", binding_epoch=1, record_version=1,
        ))
        db.flush()
        db.add(MailConnection(
            id=mail_ids[index], organization_id=organization.id, identity_id=identity_ids[index],
            namespace=f"mail-{index}", state="active", record_version=1,
        ))
    db.commit()
    return db, reviewer, outsider, organization, projects, mail_ids


def command(key: str, *, expected=1, decision="confirm", **changes):
    return ContactResolutionCommand(
        decision_key=key, expected_record_version=expected, decision=decision,
        reason_code="reviewed_by_operator", **changes,
    )


def test_normalization_is_deterministic_and_phone_is_e164_like():
    assert normalize_email(" Клиент <SALES@пример.рф> ") == "sales@xn--e1afmkfd.xn--p1ai"
    assert normalize_phone("8 (999) 123-45-67") == "+79991234567"
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"


def test_same_contact_same_scope_produces_one_explainable_proposal(world):
    db, reviewer, _, _, projects, mailboxes = world
    first = discover_contact_from_message(
        db, projects[0].id, "Sales <SALES@supplier.example>", "Synthetic", reviewer,
        mail_connection_id=mailboxes[0],
    )
    second = discover_contact_from_message(
        db, projects[0].id, "sales@supplier.example", "Synthetic again", reviewer,
        mail_connection_id=mailboxes[0],
    )
    assert first.id == second.id
    assert first.resolution_state == "proposed"
    assert first.resolution_reason_code == "gmail_sender_candidate"
    assert first.normalized_domain == "supplier.example"
    assert len(list(db.scalars(select(ProjectContact)))) == 1


def test_same_email_is_isolated_by_mailbox_and_project(world):
    db, reviewer, _, _, projects, mailboxes = world
    first = discover_contact_from_message(
        db, projects[0].id, "same@supplier.example", "One", reviewer,
        mail_connection_id=mailboxes[0],
    )
    second = discover_contact_from_message(
        db, projects[1].id, "SAME@supplier.example", "Two", reviewer,
        mail_connection_id=mailboxes[1],
    )
    assert first.id != second.id
    assert (first.project_id, first.mail_connection_id) != (second.project_id, second.mail_connection_id)


def test_human_confirmation_is_cas_guarded_and_replay_safe(world):
    db, reviewer, _, _, projects, mailboxes = world
    row = discover_contact_from_message(
        db, projects[0].id, "sales@supplier.example", "One", reviewer,
        mail_connection_id=mailboxes[0],
    )
    request = command("decision:contact:0001")
    result = resolve_contact(row.id, request, db, reviewer)
    assert result["record_version"] == 2 and result["confirmed"] is True
    replay = resolve_contact(row.id, request, db, reviewer)
    assert replay["already_applied"] is True
    history = list(db.scalars(select(ProjectContactHistory)))
    assert len(history) == 1
    assert (history[0].from_state, history[0].to_state) == ("proposed", "confirmed")
    with pytest.raises(HTTPException) as error:
        resolve_contact(row.id, command("decision:contact:0002", expected=1), db, reviewer)
    assert error.value.status_code == 409


def test_conflicting_project_binding_requires_separate_review(world):
    db, reviewer, _, _, projects, mailboxes = world
    first = discover_contact_from_message(
        db, projects[0].id, "shared@supplier.example", "One", reviewer,
        mail_connection_id=mailboxes[0],
    )
    resolve_contact(first.id, command("decision:contact:1001"), db, reviewer)
    second = discover_contact_from_message(
        db, projects[1].id, "shared@supplier.example", "Two", reviewer,
        mail_connection_id=mailboxes[0],
    )
    with pytest.raises(HTTPException) as error:
        resolve_contact(second.id, command("decision:contact:1002"), db, reviewer)
    assert error.value.status_code == 409
    assert second.confirmed is False
    # A conflicting proposal never becomes a deterministic routing decision.
    assert contact_for_sender(
        db, projects[1].id, "shared@supplier.example", reviewer,
        mail_connection_id=mailboxes[0],
    ).id == first.id


def test_correction_normalizes_values_and_records_only_pii_safe_history(world):
    db, reviewer, _, _, projects, mailboxes = world
    row = discover_contact_from_message(
        db, projects[0].id, "old@supplier.example", "One", reviewer,
        mail_connection_id=mailboxes[0],
    )
    result = resolve_contact(row.id, command(
        "decision:contact:2001", decision="correct",
        email="Person <NEW@Supplier.Example>", phone="8 999 111 22 33", company="Supplier",
    ), db, reviewer)
    assert result["email"] == "new@supplier.example"
    assert result["phone"] == "+79991112233"
    history = db.scalar(select(ProjectContactHistory))
    assert set(history.changed_fields) >= {"email", "phone", "company", "state"}
    assert "new@supplier.example" not in history.snapshot_hash
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "project_contact_resolved"))
    assert "new@supplier.example" not in audit.details
    assert "79991112233" not in audit.details


def test_history_is_append_only(world):
    db, reviewer, _, _, projects, mailboxes = world
    row = discover_contact_from_message(
        db, projects[0].id, "sales@supplier.example", "One", reviewer,
        mail_connection_id=mailboxes[0],
    )
    resolve_contact(row.id, command("decision:contact:3001"), db, reviewer)
    history = db.scalar(select(ProjectContactHistory))
    history.reason_code = "tampered"
    with pytest.raises(ValueError, match="append_only_record"):
        db.commit()
    db.rollback()


def test_outsider_cannot_resolve_contact(world):
    db, reviewer, outsider, _, projects, mailboxes = world
    row = discover_contact_from_message(
        db, projects[0].id, "sales@supplier.example", "One", reviewer,
        mail_connection_id=mailboxes[0],
    )
    with pytest.raises(HTTPException) as error:
        resolve_contact(row.id, command("decision:contact:4001"), db, outsider)
    assert error.value.status_code == 403
    assert row.confirmed is False


def test_legacy_patch_cannot_bypass_cas_history(world):
    db, reviewer, _, _, projects, mailboxes = world
    row = discover_contact_from_message(
        db, projects[0].id, "sales@supplier.example", "One", reviewer,
        mail_connection_id=mailboxes[0],
    )
    with pytest.raises(HTTPException) as error:
        update_contact(row.id, ContactUpdate(confirmed=True), db, reviewer)
    assert error.value.status_code == 409
    assert row.confirmed is False


def test_decision_key_collision_fails_closed(world):
    db, reviewer, _, _, projects, mailboxes = world
    first = discover_contact_from_message(
        db, projects[0].id, "one@supplier.example", "One", reviewer,
        mail_connection_id=mailboxes[0],
    )
    second = discover_contact_from_message(
        db, projects[0].id, "two@supplier.example", "Two", reviewer,
        mail_connection_id=mailboxes[0],
    )
    resolve_contact(first.id, command("decision:contact:5001"), db, reviewer)
    with pytest.raises(HTTPException) as error:
        resolve_contact(second.id, command("decision:contact:5001"), db, reviewer)
    assert error.value.status_code == 409
