import base64
from email import message_from_bytes, policy

import pytest

from app.integrations.contracts import MailNotAppliedError, MailSendCommand, MailboxAdapter
from app.integrations.gmail_mail import GmailMailboxAdapter


class Execute:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class Labels:
    def list(self, **kwargs):
        assert kwargs == {"userId": "me"}
        return Execute({"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]})


class Messages:
    def __init__(self):
        self.body = None
        self.move = None

    def send(self, **kwargs):
        assert kwargs["userId"] == "me"
        self.body = kwargs["body"]
        return Execute({"id": "gm-1", "threadId": "thread-1"})

    def modify(self, **kwargs):
        self.move = ("modify", kwargs)
        return Execute({"id": kwargs["id"]})

    def trash(self, **kwargs):
        self.move = ("trash", kwargs)
        return Execute({"id": kwargs["id"]})


class Users:
    def __init__(self):
        self.messages_api = Messages()

    def labels(self):
        return Labels()

    def messages(self):
        return self.messages_api


class Service:
    def __init__(self):
        self.users_api = Users()

    def users(self):
        return self.users_api


class Workspace:
    def __init__(self, scopes=None):
        self.gmail = Service()
        self.scopes = frozenset(scopes or {
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
        })

    def health(self):
        from app.integrations.contracts import AdapterHealth
        return AdapterHealth(True, "ready")

    def authorized_scopes(self):
        return self.scopes

    def service(self, api, version):
        assert (api, version) == ("gmail", "v1")
        return self.gmail


def test_gmail_adapter_implements_mailbox_protocol_and_lists_labels():
    adapter = GmailMailboxAdapter(Workspace())
    assert isinstance(adapter, MailboxAdapter)
    assert [(folder.id, folder.name, folder.kind) for folder in adapter.list_folders()] == [
        ("INBOX", "INBOX", "system")
    ]


def test_gmail_adapter_builds_to_cc_bcc_and_thread_without_logging_content():
    workspace = Workspace()
    adapter = GmailMailboxAdapter(workspace)
    receipt = adapter.send_message(MailSendCommand(
        to=("to@example.test",),
        cc=("cc@example.test",),
        bcc=("bcc@example.test",),
        subject="Synthetic subject",
        body="Synthetic body",
        thread_id="thread-1",
    ))
    assert receipt.external_message_id == "gm-1"
    assert receipt.external_thread_id == "thread-1"
    request = workspace.gmail.users_api.messages_api.body
    assert request["threadId"] == "thread-1"
    parsed = message_from_bytes(base64.urlsafe_b64decode(request["raw"]), policy=policy.default)
    assert parsed["To"] == "to@example.test"
    assert parsed["Cc"] == "cc@example.test"
    assert parsed["Bcc"] == "bcc@example.test"
    assert parsed.get_content().strip() == "Synthetic body"


def test_gmail_adapter_sends_safe_html_alternative_and_moves_messages():
    workspace = Workspace()
    adapter = GmailMailboxAdapter(workspace)
    adapter.send_message(MailSendCommand(
        to=("to@example.test",), cc=(), bcc=(), subject="Formatted",
        body="Synthetic body", html_body="<div><strong>Synthetic body</strong></div>",
    ))
    request = workspace.gmail.users_api.messages_api.body
    parsed = message_from_bytes(base64.urlsafe_b64decode(request["raw"]), policy=policy.default)
    assert parsed.is_multipart()
    assert parsed.get_body(preferencelist=("plain",)).get_content().strip() == "Synthetic body"
    assert "<strong>Synthetic body</strong>" in parsed.get_body(preferencelist=("html",)).get_content()

    receipt = adapter.move_message("gm-1", "spam")
    assert receipt.destination == "spam"
    assert workspace.gmail.users_api.messages_api.move == (
        "modify",
        {"userId": "me", "id": "gm-1", "body": {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}},
    )


def test_gmail_adapter_fails_closed_for_move_without_modify_scope():
    workspace = Workspace(scopes={
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    })
    adapter = GmailMailboxAdapter(workspace)
    assert adapter.capabilities()["move"] is False
    with pytest.raises(MailNotAppliedError) as error:
        adapter.move_message("gm-1", "trash")
    assert str(error.value) == "gmail_modify_scope_required"
    assert workspace.gmail.users_api.messages_api.move is None


def test_gmail_adapter_classifies_service_setup_failure_as_not_applied():
    class BrokenWorkspace(Workspace):
        def service(self, api, version):
            raise RuntimeError("credential secret must not escape")

    adapter = GmailMailboxAdapter(BrokenWorkspace())
    with pytest.raises(MailNotAppliedError) as error:
        adapter.send_message(MailSendCommand(
            to=("to@example.test",), cc=(), bcc=(), subject="Synthetic", body="Body",
        ))
    assert "secret" not in str(error.value)
