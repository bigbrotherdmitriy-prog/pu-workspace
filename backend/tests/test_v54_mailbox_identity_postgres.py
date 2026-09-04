"""S02 acceptance on an explicitly disposable PostgreSQL database.

The test uses a fresh random schema, a fake provider, and synthetic metadata.
Provider locators and message content are never written to test output or audit.
"""
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database import Base
from app.mailbox_identity.runtime import runtime_for_message
from app.mailbox_identity.service import MailboxIdentityService
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import (
    MailboxAuthorityState,
    MailboxCutoverFlags,
    MailboxOriginBinding,
    MailboxOriginCurrent,
)
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_pilot import ConnectionIdentity, MailConnection, SourceReference


def _require(condition: bool) -> None:
    if not condition:
        raise AssertionError("s02_invariant_failed")


@pytest.fixture
def mailbox_pg_engine():
    raw = os.getenv("PUW_V54_MAILBOX_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("CONDITIONAL: explicit isolated PostgreSQL URL not supplied")
    url = make_url(raw)
    _require(url.get_backend_name() == "postgresql")
    _require(url.host in {"localhost", "127.0.0.1", "::1"} or (
        os.getenv("GITHUB_ACTIONS") == "true" and url.host == "postgres"
    ))
    _require(bool(url.database and url.database.startswith("puw_v54_test_")))
    _require(not url.query)

    schema = "mailbox_s02_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    created = False

    @event.listens_for(engine, "connect")
    def isolate(connection, _record):
        cursor = connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.execute("SET statement_timeout TO '20s'")
        cursor.close()
        connection.commit()

    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_s02_same_provider_message_and_thread_are_isolated_per_mailbox(
        mailbox_pg_engine, monkeypatch):
    from app.api import ai_secretary, gmail

    provider_message = "synthetic-shared-external-id"
    provider_thread = "synthetic-shared-thread-id"
    message_content = "synthetic mailbox acceptance content"
    observed_token_ids: list[int] = []

    item = {
        "id": provider_message,
        "threadId": provider_thread,
        "historyId": "synthetic-observation",
        "labelIds": ["INBOX"],
        "snippet": message_content,
        "payload": {"mimeType": "text/plain", "headers": []},
    }

    class FakeGmail:
        def users(self):
            return self

        def messages(self):
            return self

        def list(self, **_kwargs):
            return SimpleNamespace(execute=lambda: {"messages": [{"id": provider_message}]})

        def get(self, **_kwargs):
            return SimpleNamespace(execute=lambda: item)

    def mailbox_adapter(token_id, _db):
        observed_token_ids.append(token_id)
        return SimpleNamespace(service=lambda *_args: FakeGmail())

    monkeypatch.setattr(gmail, "google_workspace_for_project",
                        lambda *_args, **_kwargs: pytest.fail("project_fallback"))
    monkeypatch.setattr(gmail, "google_workspace_for_mailbox", mailbox_adapter)
    monkeypatch.setattr(gmail, "project_candidate",
                        lambda _db, project_id, *_args: (project_id, 0.4, "synthetic"))
    monkeypatch.setattr(gmail, "contact_for_sender", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gmail, "notify_telegram", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai_secretary, "create_tasks_from_files", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_secretary, "create_response_drafts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_secretary, "create_governance_items",
                        lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(ai_secretary, "brief_summary", lambda *_args, **_kwargs: "Synthetic")
    monkeypatch.setattr(ai_secretary, "configured_action_adapter",
                        lambda *_args, **_kwargs: SimpleNamespace(provider="test"))

    with Session(mailbox_pg_engine) as db:
        user = User(name="Synthetic operator", email="s02-operator@example.test", is_admin=False)
        organization = Organization(name="Synthetic S02 organization")
        db.add_all([user, organization])
        db.flush()
        projects = [
            Project(name="Synthetic alpha", organization_id=organization.id),
            Project(name="Synthetic beta", organization_id=organization.id),
        ]
        db.add_all(projects)
        db.flush()
        db.add_all(ProjectMember(project_id=project.id, user_id=user.id, role="manager")
                   for project in projects)
        tokens = [GoogleOAuthToken(project_id=project.id) for project in projects]
        db.add_all(tokens)
        db.flush()

        mailbox_rows = []
        for index, token in enumerate(tokens):
            identity, connection, generation = MailboxIdentityService().bind_verified_google_subject(
                db,
                organization_id=organization.id,
                google_token_id=token.id,
                subject=f"synthetic-subject-{index}",
            )
            flags = db.scalar(select(MailboxCutoverFlags).where(
                MailboxCutoverFlags.organization_id == organization.id,
                MailboxCutoverFlags.mail_connection_id == connection.id,
                MailboxCutoverFlags.credential_generation == generation,
            ))
            flags.shadow_write = True
            flags.shadow_read_compare = True
            flags.pilot_write = True
            flags.primary_read = True
            db.add(MailboxAuthorityState(
                organization_id=organization.id,
                mail_connection_id=connection.id,
                principal_kind="user",
                principal_id=str(user.id),
                permissions=["ingest", "read"],
                state="active",
                authority_version=1,
                valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            mailbox_rows.append((identity, connection))
        db.commit()

        results = [gmail.sync_gmail_project(
            project.id, db, user, query="synthetic", max_results=1,
        ) for project in projects]
        success = {"processed": 1, "skipped": 0, "failed": 0, "errors": []}
        _require(all(result == success for result in results))

        messages = list(db.scalars(select(Message).order_by(Message.id)))
        sources = list(db.scalars(select(SourceReference).order_by(SourceReference.id)))
        currents = list(db.scalars(select(MailboxOriginCurrent).order_by(
            MailboxOriginCurrent.message_id)))
        bindings = list(db.scalars(select(MailboxOriginBinding).order_by(
            MailboxOriginBinding.message_id)))

        _require(len(messages) == len(sources) == len(currents) == len(bindings) == 2)
        _require(len({message.id for message in messages}) == 2)
        _require({message.project_id for message in messages} == {project.id for project in projects})
        _require({message.mail_connection_id for message in messages}
                 == {connection.id for _identity, connection in mailbox_rows})
        _require(len({message.source_reference_id for message in messages}) == 2)
        _require(set(observed_token_ids) == {token.id for token in tokens})

        current_by_message = {current.message_id: current for current in currents}
        binding_by_id = {binding.id: binding for binding in bindings}
        for message in messages:
            current = current_by_message[message.id]
            binding = binding_by_id[current.binding_id]
            source = db.get(SourceReference, message.source_reference_id)
            connection = db.get(MailConnection, message.mail_connection_id)
            identity = db.get(ConnectionIdentity, source.identity_id)
            runtime = runtime_for_message(db, message, actor=user)
            _require(binding.message_id == message.id)
            _require(binding.mail_connection_id == message.mail_connection_id)
            _require(binding.source_reference_id == message.source_reference_id)
            _require(connection.identity_id == identity.id == runtime.identity_id)
            _require(source.origin_project_id == message.project_id)
            _require(source.canonical_locator.get("provider_thread_id") == provider_thread)
            _require(runtime.mail_connection_id == message.mail_connection_id)
            _require(runtime.source_reference_id == message.source_reference_id)

        audit_details = "\n".join(row.details or "" for row in db.scalars(select(AuditLog)))
        _require(provider_message not in audit_details)
        _require(provider_thread not in audit_details)
        _require(message_content not in audit_details)
        _require("@" not in audit_details)
