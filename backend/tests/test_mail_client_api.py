import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import mail
from app.integrations.contracts import AdapterHealth, MailFolder, MailNotAppliedError, MailSendReceipt
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


class FakeMailbox:
    provider = "fake_mail"

    def __init__(self, outcome="sent"):
        self.outcome = outcome
        self.commands = []

    def health(self):
        return AdapterHealth(True, "ready")

    def list_folders(self):
        return [MailFolder("TEAM", "Команда", "user")]

    def send_message(self, command):
        self.commands.append(command)
        if self.outcome == "not_applied":
            raise MailNotAppliedError("rejected")
        if self.outcome == "unknown":
            raise TimeoutError("secret provider detail")
        return MailSendReceipt("provider-message-1", "provider-thread-1")


@pytest.fixture
def mail_context(db_session):
    organization = Organization(name="Synthetic org")
    user = User(name="Operator", email="operator@example.test")
    other_user = User(name="Other", email="other@example.test")
    db_session.add_all([organization, user, other_user])
    db_session.flush()
    project = Project(name="Project A", organization_id=organization.id)
    other_project = Project(name="Project B", organization_id=organization.id)
    db_session.add_all([project, other_project])
    db_session.flush()
    db_session.add_all([
        ProjectMember(project_id=project.id, user_id=user.id, role="owner"),
        ProjectMember(project_id=other_project.id, user_id=other_user.id, role="owner"),
    ])
    contract = Contract(project_id=project.id, number="C-1", title="Synthetic contract")
    db_session.add(contract)
    db_session.flush()
    incoming = Message(
        organization_id=organization.id,
        project_id=project.id,
        contract_id=contract.id,
        created_by_user_id=user.id,
        source_type="email",
        source_external_id="gmail-message-1",
        source_name="Supplier - Schedule",
        source_url="https://mail.google.com/mail/u/0/#all/gmail-message-1",
        source_sender="Supplier <supplier@example.test>",
        source_thread_id="gmail-thread-1",
        content="Please review the schedule.",
        attachments_json=json.dumps([{"name": "plan.pdf", "mime_type": "application/pdf", "size": 42}]),
        mail_headers_json=json.dumps({"to": "operator@example.test", "message-id": "<m1@example.test>"}),
        mail_labels_json=json.dumps(["INBOX"]),
        summary="Review requested",
        context_confidence=1,
        context_evidence="synthetic fixture",
        context_confirmed=True,
        status="ready",
    )
    archived = Message(
        organization_id=organization.id,
        project_id=project.id,
        created_by_user_id=user.id,
        source_type="email",
        source_external_id="gmail-message-2",
        source_name="Archived note",
        source_thread_id="gmail-thread-1",
        content="Prior context",
        attachments_json="[]",
        mail_headers_json="{}",
        mail_labels_json=json.dumps(["IMPORTANT"]),
        summary="Prior note",
        context_confidence=1,
        context_evidence="synthetic fixture",
        context_confirmed=True,
        status="ready",
    )
    db_session.add_all([incoming, archived])
    db_session.commit()
    return SimpleNamespace(
        db=db_session, user=user, other_user=other_user, project=project,
        other_project=other_project, contract=contract, incoming=incoming, archived=archived,
    )


def _new_draft(context, **overrides):
    values = {
        "project_id": context.project.id,
        "contract_id": context.contract.id,
        "mode": "reply_all",
        "reply_to_message_id": context.incoming.id,
        "to": ["supplier@example.test"],
        "cc": ["manager@example.test"],
        "bcc": [],
        "subject": "Schedule",
        "body": "Approved response body.",
    }
    values.update(overrides)
    return mail.create_mail_draft(mail.MailDraftCreate(**values), context.db, context.user)


def test_routes_expose_provider_neutral_mail_client_contract():
    paths = {route.path for route in mail.router.routes}
    assert {
        "/mail/projects/{project_id}/capabilities",
        "/mail/projects/{project_id}/folders",
        "/mail/projects/{project_id}/messages",
        "/mail/projects/{project_id}/threads",
        "/mail/projects/{project_id}/threads/{thread_id}",
        "/mail/projects/{project_id}/drafts",
        "/mail/messages/{message_id}",
        "/mail/drafts",
        "/mail/drafts/{draft_id}",
        "/mail/drafts/{draft_id}/approve",
        "/mail/drafts/{draft_id}/send",
        "/mail/drafts/{draft_id}/retry",
    }.issubset(paths)


def test_mailbox_list_search_thread_and_folder_are_project_scoped(mail_context):
    inbox = mail.mail_messages(mail_context.project.id, "inbox", "supplier", 50, None,
                               mail_context.db, mail_context.user)
    assert [item["id"] for item in inbox["messages"]] == [mail_context.incoming.id]
    assert inbox["messages"][0]["thread_id"] == "gmail-thread-1"
    assert inbox["messages"][0]["attachments"][0]["name"] == "plan.pdf"

    archive = mail.mail_messages(mail_context.project.id, "archive", None, 50, None,
                                 mail_context.db, mail_context.user)
    assert [item["id"] for item in archive["messages"]] == [mail_context.archived.id]
    thread = mail.mail_thread(mail_context.project.id, "gmail-thread-1", mail_context.db, mail_context.user)
    assert [item["id"] for item in thread["messages"]] == [mail_context.incoming.id, mail_context.archived.id]

    with pytest.raises(HTTPException) as denied:
        mail.mail_messages(mail_context.project.id, "inbox", None, 50, None,
                           mail_context.db, mail_context.other_user)
    assert denied.value.status_code == 403


def test_single_message_fallback_thread_can_be_opened(mail_context):
    row = Message(
        organization_id=mail_context.project.organization_id,
        project_id=mail_context.project.id,
        created_by_user_id=mail_context.user.id,
        source_type="email",
        source_external_id="singleton-message",
        source_name="Singleton",
        source_sender="sender@example.test",
        source_thread_id=None,
        content="Synthetic singleton",
        attachments_json="[]",
        summary="Synthetic",
        context_confidence=1,
        context_evidence="fixture",
        context_confirmed=True,
        status="ready",
    )
    mail_context.db.add(row)
    mail_context.db.commit()
    result = mail.mail_thread(
        mail_context.project.id, f"message:{row.id}", mail_context.db, mail_context.user,
    )
    assert [item["id"] for item in result["messages"]] == [row.id]


def test_folder_adapter_errors_are_safe(monkeypatch, mail_context):
    adapter = FakeMailbox()
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    result = mail.mail_folders(mail_context.project.id, mail_context.db, mail_context.user)
    assert result["provider"] == "fake_mail"
    assert any(item["id"] == "TEAM" for item in result["folders"])

    monkeypatch.setattr(adapter, "list_folders", lambda: (_ for _ in ()).throw(RuntimeError("token=secret")))
    with pytest.raises(HTTPException) as error:
        mail.mail_folders(mail_context.project.id, mail_context.db, mail_context.user)
    assert error.value.detail == "mail_provider_unavailable"
    assert "secret" not in str(error.value)


def test_edit_invalidates_approval_and_revision_conflicts(mail_context):
    created = _new_draft(mail_context)
    message = mail.mail_message(mail_context.incoming.id, mail_context.db, mail_context.user)
    assert message["drafts"][0]["id"] == created["id"]
    assert mail.mail_drafts(mail_context.project.id, "draft", mail_context.db, mail_context.user)["count"] == 1
    approved = mail.approve_mail_draft(
        created["id"], mail.MailDraftApproval(revision=1), mail_context.db, mail_context.user,
    )
    assert approved["approved_revision"] == 1

    revised = mail.update_mail_draft(
        created["id"], mail.MailDraftPatch(expected_revision=1, body="Updated body."),
        mail_context.db, mail_context.user,
    )
    assert revised["revision"] == 2
    assert revised["approved_revision"] is None
    assert revised["status"] == "draft"

    with pytest.raises(HTTPException) as conflict:
        mail.approve_mail_draft(created["id"], mail.MailDraftApproval(revision=1),
                                mail_context.db, mail_context.user)
    assert conflict.value.status_code == 409


def test_send_requires_current_approval_is_idempotent_and_preserves_headers(monkeypatch, mail_context):
    adapter = FakeMailbox()
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    created = _new_draft(mail_context)
    command = mail.MailDraftSend(revision=1, idempotency_key="send-command-0001")
    with pytest.raises(HTTPException) as unapproved:
        mail.send_mail_draft(created["id"], command, mail_context.db, mail_context.user)
    assert unapproved.value.detail == "mail_current_revision_not_approved"

    mail.approve_mail_draft(created["id"], mail.MailDraftApproval(revision=1),
                            mail_context.db, mail_context.user)
    sent = mail.send_mail_draft(created["id"], command, mail_context.db, mail_context.user)
    replay = mail.send_mail_draft(created["id"], command, mail_context.db, mail_context.user)
    assert sent["status"] == "sent"
    assert sent["receipt"]["external_message_id"] == "provider-message-1"
    assert replay["idempotent_replay"] is True
    assert len(adapter.commands) == 1
    assert adapter.commands[0].to == ("supplier@example.test",) or list(adapter.commands[0].to) == ["supplier@example.test"]
    assert list(adapter.commands[0].cc) == ["manager@example.test"]
    assert adapter.commands[0].thread_id == "gmail-thread-1"

    audits = mail_context.db.query(AuditLog).filter(AuditLog.entity_id == created["id"]).all()
    assert {row.action for row in audits} >= {"mail_draft_created", "mail_draft_approved", "mail_send_started", "mail_sent"}
    assert all("supplier@example.test" not in (row.details or "") for row in audits)


def test_unknown_outcome_blocks_retry_and_does_not_leak_provider_error(monkeypatch, mail_context):
    adapter = FakeMailbox("unknown")
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    created = _new_draft(mail_context)
    mail.approve_mail_draft(created["id"], mail.MailDraftApproval(revision=1),
                            mail_context.db, mail_context.user)
    command = mail.MailDraftSend(revision=1, idempotency_key="send-command-unknown")
    result = mail.send_mail_draft(created["id"], command, mail_context.db, mail_context.user)
    assert result["status"] == "unknown"
    assert result["error_code"] == "provider_outcome_unknown"
    assert "secret" not in json.dumps(result, default=str)
    with pytest.raises(HTTPException) as blocked:
        mail.retry_mail_draft(created["id"], mail.MailDraftSend(
            revision=1, idempotency_key="send-command-retry-1"), mail_context.db, mail_context.user)
    assert blocked.value.detail == "mail_send_outcome_unknown_reconciliation_required"
    assert len(adapter.commands) == 1


def test_proven_not_applied_can_be_retried_with_new_command(monkeypatch, mail_context):
    adapter = FakeMailbox("not_applied")
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    created = _new_draft(mail_context)
    mail.approve_mail_draft(created["id"], mail.MailDraftApproval(revision=1),
                            mail_context.db, mail_context.user)
    failed = mail.send_mail_draft(created["id"], mail.MailDraftSend(
        revision=1, idempotency_key="send-command-failed"), mail_context.db, mail_context.user)
    assert failed["status"] == "failed"
    adapter.outcome = "sent"
    sent = mail.retry_mail_draft(created["id"], mail.MailDraftSend(
        revision=1, idempotency_key="send-command-retry-2"), mail_context.db, mail_context.user)
    assert sent["status"] == "sent"
    assert len(adapter.commands) == 2


def test_attachment_metadata_is_visible_but_send_fails_closed(mail_context):
    created = _new_draft(mail_context, attachments=[{
        "message_id": mail_context.incoming.id,
        "attachment_index": 0,
    }])
    assert created["attachments"] == [{
        "message_id": mail_context.incoming.id,
        "attachment_index": 0,
        "name": "plan.pdf",
        "mime_type": "application/pdf",
        "size": 42,
        "sendable": False,
    }]
    mail.approve_mail_draft(created["id"], mail.MailDraftApproval(revision=1),
                            mail_context.db, mail_context.user)
    with pytest.raises(HTTPException) as blocked:
        mail.send_mail_draft(created["id"], mail.MailDraftSend(
            revision=1, idempotency_key="attachment-send-1"), mail_context.db, mail_context.user)
    assert blocked.value.detail == "mail_attachment_send_requires_verified_mailbox_origin"
