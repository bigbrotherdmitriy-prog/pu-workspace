import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register all mapped tables
from app.core.auth import require_user
from app.database import Base, get_db

from app.api import mail
from app.integrations.contracts import AdapterHealth, MailFolder, MailNotAppliedError, MailSendReceipt
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.response_draft import ResponseDraft
from app.models.google_token import GoogleOAuthToken
from app.models.job import BackgroundJob
from app.models.v54_provider_action import ProviderAction
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

    def move_message(self, external_message_id, destination):
        self.commands.append((external_message_id, destination))
        return SimpleNamespace(external_message_id=external_message_id, destination=destination)


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
    db_session.add(GoogleOAuthToken(
        project_id=project.id, access_token=None, refresh_token=None,
        scopes="https://www.googleapis.com/auth/gmail.send",
    ))
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


@pytest.mark.parametrize("role,expected_status", [("editor", 403), ("manager", 200)])
def test_mail_draft_approval_requires_manager_via_http(tmp_path, role, expected_status):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mail-role.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="Synthetic org")
        editor = User(name="Editor", email="editor@example.test")
        db.add_all([organization, editor])
        db.flush()
        project = Project(name="Synthetic project", organization_id=organization.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=editor.id, role=role))
        draft = ResponseDraft(
            project_id=project.id,
            reviewer_user_id=editor.id,
            subject="Synthetic subject",
            body="Synthetic body",
            recipient_to="recipient@example.test",
            status="draft",
            source_file_id="synthetic-message:1",
            source_file_name="Synthetic message",
            source_excerpt="Synthetic excerpt",
            source_excerpt_hash="a" * 64,
            confidence=1.0,
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id
        editor_id = editor.id

    app = FastAPI()
    app.include_router(mail.router)

    def test_db():
        with Session(engine) as db:
            yield db

    def test_editor():
        with Session(engine) as db:
            return db.get(User, editor_id)

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[require_user] = test_editor
    try:
        with TestClient(app) as client:
            response = client.post(f"/mail/drafts/{draft_id}/approve", json={"revision": 1})
        assert response.status_code == expected_status
        with Session(engine) as db:
            saved = db.get(ResponseDraft, draft_id)
            assert saved.status == ("draft" if role == "editor" else "approved")
            assert saved.approved_revision == (None if role == "editor" else 1)
            audit_id = db.scalar(select(AuditLog.id).where(AuditLog.action == "mail_draft_approved", AuditLog.entity_id == draft_id))
            assert (audit_id is not None) == (role == "manager")
    finally:
        engine.dispose()


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
        "/mail/settings",
        "/mail/assist",
        "/mail/messages/{message_id}/move",
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
    result = mail.mail_folders(mail_context.project.id, mail_context.db, mail_context.user, True)
    assert result["provider"] == "fake_mail"
    assert any(item["id"] == "TEAM" for item in result["folders"])

    monkeypatch.setattr(adapter, "list_folders", lambda: (_ for _ in ()).throw(RuntimeError("token=secret")))
    degraded = mail.mail_folders(mail_context.project.id, mail_context.db, mail_context.user, True)
    assert degraded["provider_available"] is False
    assert degraded["provider_error"] == "temporarily_unavailable"
    assert any(item["id"] == "inbox" for item in degraded["folders"])
    assert all(item["id"] != "TEAM" for item in degraded["folders"])
    assert "secret" not in str(degraded)


def test_core_folder_view_does_not_spend_provider_quota(monkeypatch, mail_context):
    adapter = FakeMailbox()
    monkeypatch.setattr(
        adapter,
        "list_folders",
        lambda: (_ for _ in ()).throw(AssertionError("live provider must not be called")),
    )
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)

    result = mail.mail_folders(mail_context.project.id, mail_context.db, mail_context.user)

    assert result["provider_available"] is True
    assert result["provider_folders_loaded"] is False
    assert any(item["id"] == "inbox" for item in result["folders"])
    assert all(item["id"] != "TEAM" for item in result["folders"])


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
    assert sent["status"] == "queued"
    assert sent["receipt"] is None
    assert replay["idempotent_replay"] is True
    assert adapter.commands == []
    assert mail_context.db.query(BackgroundJob).filter(BackgroundJob.id == sent["job_id"]).one().kind == "provider.action.dispatch"

    audits = mail_context.db.query(AuditLog).filter(AuditLog.entity_id == created["id"]).all()
    assert {row.action for row in audits} >= {"mail_draft_created", "mail_draft_approved", "mail_send_queued"}
    assert all("supplier@example.test" not in (row.details or "") for row in audits)


def test_rich_html_is_sanitized_and_sent_as_multipart(monkeypatch, mail_context):
    adapter = FakeMailbox()
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    created = _new_draft(
        mail_context,
        body_format="html",
        body='<div style="font-family:Arial;font-size:14px;color:#123456" onclick="secret()"><strong>Готово</strong> <script>token</script><a href="javascript:bad">ссылка</a></div>',
    )
    assert created["body_format"] == "html"
    assert "onclick" not in created["body"]
    assert "script" not in created["body"]
    assert "javascript" not in created["body"]
    mail.approve_mail_draft(created["id"], mail.MailDraftApproval(revision=1), mail_context.db, mail_context.user)
    queued = mail.send_mail_draft(created["id"], mail.MailDraftSend(
        revision=1, idempotency_key="rich-send-command-1"), mail_context.db, mail_context.user)
    assert queued["status"] == "queued" and adapter.commands == []
    draft = mail_context.db.get(ResponseDraft, created["id"])
    assert "<strong>Готово</strong>" in draft.body
    assert "Готово" in draft.body


def test_mail_settings_are_user_scoped_and_signature_is_sanitized(mail_context):
    payload = mail.MailSettingsPatch(
        display_name="Operator",
        signature_html='<div><b>Иван</b><img src="https://tracker.test/pixel"><script>secret</script></div>',
        auto_signature_new=True,
        auto_signature_reply=False,
        default_font="Calibri",
        default_font_size="16px",
        default_text_color="#123456",
    )
    saved = mail.update_mail_settings(payload, mail_context.db, mail_context.user)
    assert "<b>Иван</b>" in saved["signature_html"]
    assert "tracker" not in saved["signature_html"]
    assert "secret" not in saved["signature_html"]
    assert saved["auto_signature_reply"] is False
    other = mail.get_mail_settings(mail_context.db, mail_context.other_user)
    assert other["signature_html"] == ""


def test_gemini_mail_assist_uses_policy_and_never_sends(monkeypatch, mail_context):
    class FakeAI:
        provider = "gemini"
        model = "gemini-test"

        def health(self):
            return SimpleNamespace(ready=True)

        def compose_message(self, text, context_name, action, tone):
            assert "Please review the schedule" in text
            assert action == "reply"
            assert tone == "business"
            return {"subject": "Re: Schedule", "body": "Добрый день! Срок проверяем.", "notes": "Проверьте дату."}

    provider = FakeAI()
    mailbox = FakeMailbox()
    monkeypatch.setattr(mail, "configured_ai_provider", lambda: provider)
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: mailbox)
    result = mail.assist_mail_draft(mail.MailAssistRequest(
        project_id=mail_context.project.id,
        reply_to_message_id=mail_context.incoming.id,
        action="reply",
        tone="business",
        instruction="Подготовить ответ",
        subject="Schedule",
        body="",
    ), mail_context.db, mail_context.user)
    assert result["body"] == "Добрый день! Срок проверяем."
    assert result["requires_confirmation"] is True
    assert mailbox.commands == []


@pytest.mark.parametrize("destination,label", [("spam", "SPAM"), ("trash", "TRASH")])
def test_mail_move_updates_provider_and_local_folder(monkeypatch, mail_context, destination, label):
    adapter = FakeMailbox()
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    moved = mail.move_mail_message(
        mail_context.incoming.id, mail.MailMoveRequest(destination=destination), mail_context.db, mail_context.user,
    )
    assert adapter.commands == [("gmail-message-1", destination)]
    assert label in json.loads(mail_context.incoming.mail_labels_json)
    assert moved["id"] == mail_context.incoming.id
    assert mail.mail_messages(mail_context.project.id, "inbox", None, 50, None,
                              mail_context.db, mail_context.user)["messages"] == []
    assert mail.mail_messages(mail_context.project.id, destination, None, 50, None,
                              mail_context.db, mail_context.user)["messages"][0]["id"] == mail_context.incoming.id


def test_move_permission_failure_preserves_local_message(monkeypatch, mail_context):
    from app.integrations.contracts import MailNotAppliedError
    adapter = FakeMailbox()
    def deny(*args):
        raise MailNotAppliedError("mail_modify_permission_required")
    adapter.move_message = deny
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    before = mail_context.incoming.mail_labels_json
    with pytest.raises(HTTPException) as error:
        mail.move_mail_message(mail_context.incoming.id, mail.MailMoveRequest(destination="trash"),
                               mail_context.db, mail_context.user)
    assert error.value.status_code == 403
    assert error.value.detail == "mail_modify_permission_required"
    assert mail_context.incoming.mail_labels_json == before


def test_unknown_outcome_blocks_retry_and_does_not_leak_provider_error(monkeypatch, mail_context):
    adapter = FakeMailbox("unknown")
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    created = _new_draft(mail_context)
    mail.approve_mail_draft(created["id"], mail.MailDraftApproval(revision=1),
                            mail_context.db, mail_context.user)
    command = mail.MailDraftSend(revision=1, idempotency_key="send-command-unknown")
    result = mail.send_mail_draft(created["id"], command, mail_context.db, mail_context.user)
    assert result["status"] == "queued"
    draft = mail_context.db.get(ResponseDraft, created["id"])
    draft.status = "unknown"
    draft.last_send_error_code = "provider_outcome_unknown"
    mail_context.db.commit()
    assert "secret" not in json.dumps(result, default=str)
    with pytest.raises(HTTPException) as blocked:
        mail.retry_mail_draft(created["id"], mail.MailDraftSend(
            revision=1, idempotency_key="send-command-retry-1"), mail_context.db, mail_context.user)
    assert blocked.value.detail == "mail_send_outcome_unknown_reconciliation_required"
    assert adapter.commands == []


def test_proven_not_applied_can_be_retried_with_new_command(monkeypatch, mail_context):
    adapter = FakeMailbox("not_applied")
    monkeypatch.setattr(mail, "mailbox_adapter_for_project", lambda *_: adapter)
    created = _new_draft(mail_context)
    mail.approve_mail_draft(created["id"], mail.MailDraftApproval(revision=1),
                            mail_context.db, mail_context.user)
    queued = mail.send_mail_draft(created["id"], mail.MailDraftSend(
        revision=1, idempotency_key="send-command-failed"), mail_context.db, mail_context.user)
    assert queued["status"] == "queued"
    draft = mail_context.db.get(ResponseDraft, created["id"])
    draft.status = "failed"
    action = mail_context.db.get(ProviderAction, (queued["action_id"], queued["revision"]))
    action.state = "NOT_APPLIED"
    mail_context.db.commit()
    retried = mail.retry_mail_draft(created["id"], mail.MailDraftSend(
        revision=1, idempotency_key="send-command-retry-2"), mail_context.db, mail_context.user)
    assert retried["status"] == "queued"
    assert retried["revision"] == 2
    assert adapter.commands == []


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
