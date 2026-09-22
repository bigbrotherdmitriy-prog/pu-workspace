from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.api import ai_secretary as ai
from app.core.v54_authority import AuthorityResolver, PILOT_OPERATIONS, PILOT_SCOPE
from app.database import Base
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.integration_credential import IntegrationCredential
from app.models.mailbox_identity import MailboxCredentialGeneration
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import ConnectionIdentity, MailConnection, SourceReference
from app.owner_context_confirmation import (
    OwnerContextConfirmationDenied,
    confirm_owner_context_for_auto,
    owner_context_confirmation_state,
    require_current_owner_context_confirmation,
)


NOW = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
MAIL_ID = "10000000-0000-0000-0000-000000000001"
SOURCE_ID = "20000000-0000-0000-0000-000000000001"


def seed_world(db: Session):
    db.add(Organization(id=1, name="Owner context tenant"))
    db.add_all([
        User(id=2, name="Owner", email="owner-context@example.test"),
        User(id=3, name="Editor", email="editor-context@example.test"),
    ])
    db.flush()
    db.add(Project(id=17, name="Owner context project", organization_id=1))
    db.flush()
    db.add_all([
        ProjectMember(project_id=17, user_id=2, role="owner"),
        ProjectMember(project_id=17, user_id=3, role="editor"),
        Contract(id=21, project_id=17, number="A", title="First"),
        Contract(id=22, project_id=17, number="B", title="Second"),
        AuthorityState(
            organization_id=1, project_id=17, principal_kind="user", principal_id="2",
            scope=PILOT_SCOPE, membership_role="owner", permissions=sorted(PILOT_OPERATIONS),
            state="active", authority_epoch=1, record_version=1,
            valid_until=NOW + timedelta(days=3650), updated_at=NOW, updated_by_user_id=2,
        ),
    ])
    db.flush()
    credential = IntegrationCredential(
        project_id=17, provider="google_drive", capability="storage",
        access_token="encrypted-placeholder", account_external_id="owner-context-account",
    )
    db.add(credential)
    db.flush()
    db.add(ConnectionIdentity(
        id="30000000-0000-0000-0000-000000000001",
        organization_id=1, provider="google_workspace",
        account_key="owner-context-account", state="verified",
        binding_epoch=1, record_version=1, credential_id=credential.id,
        credential_generation=1, verified_at=NOW,
    ))
    db.flush()
    db.add_all([
        MailboxCredentialGeneration(
            organization_id=1,
            connection_identity_id="30000000-0000-0000-0000-000000000001",
            generation=1, binding_epoch=1,
            integration_credential_id=credential.id, state="active", verified_at=NOW,
        ),
        MailConnection(
            id=MAIL_ID, organization_id=1,
            identity_id="30000000-0000-0000-0000-000000000001",
            namespace="gmail", state="active", record_version=1,
        ),
        SourceReference(
            id=SOURCE_ID, organization_id=1, origin_project_id=17,
            identity_id="30000000-0000-0000-0000-000000000001",
            parent_source_id=None, namespace="gmail", external_id="gmail-message-31",
            external_id_kind="stable_id", incarnation=1, object_kind="message",
            canonical_locator={
                "kind": "opaque_id", "value": "gmail-message-31",
                "normalization_version": "1",
            },
            record_version=1, freshness="fresh", sync_state="current",
            availability="available", last_seen_at=NOW, last_checked_at=NOW,
            next_check_at=NOW + timedelta(minutes=30),
            policy_pins={"access": "owner", "retention": "24h", "residency": "configured"},
            residency={"source_location": "google_workspace", "assurance": "owner_pilot"},
        ),
    ])
    db.flush()
    message = Message(
        id=31, organization_id=1, project_id=17, contract_id=21,
        created_by_user_id=2, source_type="email", source_external_id="gmail-message-31",
        source_name="Owner context message", content="Подготовь отчёт. Срок: 25.09.2026.",
        attachments_json="[]", summary="", context_confidence=0.95,
        context_evidence="AI matched project and contract", context_confirmed=True,
        status="ready", mail_connection_id=MAIL_ID, provider_message_id="gmail-message-31",
        source_reference_id=SOURCE_ID, context_version=1, origin_version=1,
    )
    db.add(message)
    db.commit()
    return message


@pytest.fixture
def world(db_session):
    return db_session, seed_world(db_session)


def owner_confirm(db, message_id=31):
    return confirm_owner_context_for_auto(
        db, message_id=message_id, project_id=17, contract_id=21,
        expected_context_version=1, user_id=2,
        authority=AuthorityResolver(clock=lambda: NOW), clock=lambda: NOW,
        correlation_id="owner-context-test",
    )


def test_high_confidence_alone_never_counts_as_owner_confirmation(world):
    db, message = world
    assert message.context_confidence == 0.95 and message.context_confirmed is True
    assert owner_context_confirmation_state(db, message) == "not_confirmed"
    with pytest.raises(OwnerContextConfirmationDenied, match="owner_context_confirmation_required"):
        require_current_owner_context_confirmation(db, message)


def test_general_editor_confirmation_does_not_authorize_auto(world):
    db, message = world
    editor = db.get(User, 3)
    ai.confirm_context(
        message.id,
        ai.ContextConfirmation(
            project_id=17, contract_id=21, expected_context_version=1,
        ),
        db,
        editor,
    )
    db.refresh(message)
    assert message.context_confirmed is True
    assert message.context_confidence == 0.95
    assert message.context_evidence == "AI matched project and contract"
    assert owner_context_confirmation_state(db, message) == "not_confirmed"


def test_separate_auto_confirmation_endpoint_records_owner_action(world):
    db, message = world
    owner = db.get(User, 2)
    payload = ai.confirm_context_for_auto(
        message.id,
        ai.AutoContextConfirmation(
            project_id=17, contract_id=21, expected_context_version=1,
        ),
        db,
        owner,
    )
    db.refresh(message)
    assert payload["state"] == "confirmed_current"
    assert payload["already_confirmed"] is False
    assert message.context_confirmed_by_user_id == owner.id


def test_explicit_current_owner_confirmation_qualifies(world):
    db, message = world
    result = owner_confirm(db)
    db.commit(); db.refresh(message)
    assert result.already_confirmed is False
    assert message.context_confirmed_by_user_id == 2
    confirmed_at = message.context_confirmed_by_user_at
    if confirmed_at.tzinfo is None:
        confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
    assert confirmed_at == NOW
    assert message.context_confirmed_context_version == message.context_version == 1
    assert message.context_confirmed_authority_epoch == 1
    assert owner_context_confirmation_state(db, message) == "confirmed_current"
    require_current_owner_context_confirmation(db, message)


def test_context_correction_increments_version_and_invalidates_owner_confirmation(world):
    db, message = world
    owner_confirm(db); db.commit(); db.refresh(message)
    owner = db.get(User, 2)
    ai.confirm_context(
        message.id,
        ai.ContextConfirmation(
            project_id=17, contract_id=22, expected_context_version=1,
        ),
        db,
        owner,
    )
    db.refresh(message)
    assert (message.contract_id, message.context_version) == (22, 2)
    assert owner_context_confirmation_state(db, message) == "not_confirmed"
    assert db.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.action == "message_auto_context_owner_confirmation_invalidated",
        AuditLog.entity_id == message.id,
    )) == 1


def test_repeated_owner_confirmation_returns_original_receipt(world):
    db, message = world
    first = owner_confirm(db); db.commit(); db.refresh(message)
    recorded_at = message.context_confirmed_by_user_at
    second = owner_confirm(db); db.commit(); db.refresh(message)
    assert first.already_confirmed is False and second.already_confirmed is True
    assert message.context_confirmed_by_user_at == recorded_at
    assert db.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.action == "message_auto_context_owner_confirmed",
        AuditLog.entity_id == message.id,
    )) == 1


@pytest.mark.parametrize("mutation", ["role", "authority_epoch"])
def test_owner_or_authority_change_makes_confirmation_stale(world, mutation):
    db, message = world
    owner_confirm(db); db.commit(); db.refresh(message)
    if mutation == "role":
        db.scalar(select(ProjectMember).where(
            ProjectMember.project_id == 17, ProjectMember.user_id == 2,
        )).role = "manager"
    else:
        authority = db.scalar(select(AuthorityState).where(
            AuthorityState.project_id == 17, AuthorityState.principal_id == "2",
        ))
        authority.authority_epoch += 1
        authority.record_version += 1
        authority.updated_at = NOW + timedelta(minutes=1)
    db.commit(); db.refresh(message)
    assert owner_context_confirmation_state(db, message) == "stale_authority"
    with pytest.raises(OwnerContextConfirmationDenied):
        require_current_owner_context_confirmation(db, message)


def test_historical_resolved_rows_are_not_backfilled(world):
    db, message = world
    assert message.context_confirmed is True
    assert message.context_confirmed_by_user_id is None
    assert message.context_confirmed_by_user_at is None
    assert message.context_confirmed_context_version is None
    assert message.context_confirmed_authority_epoch is None
    for name in (
        "context_confirmed_by_user_id", "context_confirmed_by_user_at",
        "context_confirmed_context_version", "context_confirmed_authority_epoch",
    ):
        assert Message.__table__.c[name].server_default is None


def test_postgres_concurrent_owner_confirm_and_context_correction_fail_closed():
    dsn = os.getenv("PUW_V54_INTEGRATION_DATABASE_URL")
    if not dsn:
        pytest.skip("PUW_V54_INTEGRATION_DATABASE_URL is required")
    parsed = make_url(dsn)
    assert parsed.get_backend_name() == "postgresql"
    assert (parsed.database or "").startswith("puw_v54_test_")
    schema = "owner_context_" + uuid4().hex
    admin = create_engine(dsn, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        dsn, hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000",
        },
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with sessions() as db:
        seed_world(db)
    barrier = Barrier(2)

    def confirm():
        with sessions() as db:
            barrier.wait()
            try:
                owner_confirm(db)
                db.commit()
                return "confirmed"
            except OwnerContextConfirmationDenied:
                db.rollback()
                return "conflict"

    def correct():
        with sessions() as db:
            barrier.wait()
            ai.confirm_context(
                31,
                ai.ContextConfirmation(
                    project_id=17, contract_id=22, expected_context_version=1,
                ),
                db,
                db.get(User, 2),
            )
            return "corrected"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm), pool.submit(correct)]
            outcomes = {future.result() for future in futures}
        with sessions() as db:
            message = db.get(Message, 31)
            assert message.contract_id == 22 and message.context_version == 2
            assert owner_context_confirmation_state(db, message) == "not_confirmed"
            assert "corrected" in outcomes
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
