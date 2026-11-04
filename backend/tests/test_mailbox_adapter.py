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
    def __init__(self):
        self.gmail = Service()

    def health(self):
        from app.integrations.contracts import AdapterHealth
        return AdapterHealth(True, "ready")

    def authorized_scopes(self):
        return frozenset({"https://www.googleapis.com/auth/gmail.modify"})

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


def test_google_oauth_requests_mail_modification_permission():
    from app.api.google_drive import SCOPES
    assert "https://www.googleapis.com/auth/gmail.modify" in SCOPES


@pytest.mark.parametrize("destination", ["trash", "spam", "archive", "inbox"])
def test_move_without_modify_scope_never_calls_provider(destination):
    workspace = Workspace()
    workspace.authorized_scopes = lambda: frozenset({"https://www.googleapis.com/auth/gmail.readonly"})
    with pytest.raises(MailNotAppliedError, match="mail_modify_permission_required"):
        GmailMailboxAdapter(workspace).move_message("gm-1", destination)
    assert workspace.gmail.users_api.messages_api.move is None


def test_move_setup_failure_is_safe_and_not_applied():
    workspace = Workspace()
    def broken(*args):
        raise RuntimeError("synthetic credential secret")
    workspace.service = broken
    with pytest.raises(MailNotAppliedError, match="provider_unavailable_before_move") as error:
        GmailMailboxAdapter(workspace).move_message("gm-1", "trash")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("scope", ["https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/"])
def test_move_to_trash_uses_recoverable_provider_operation(scope):
    workspace = Workspace()
    workspace.authorized_scopes = lambda: frozenset({scope})
    receipt = GmailMailboxAdapter(workspace).move_message("gm-1", "trash")
    assert receipt.destination == "trash"
    assert workspace.gmail.users_api.messages_api.move == ("trash", {"userId": "me", "id": "gm-1"})


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
def test_send_execute_failure_is_unknown_and_never_retried(failure):
    workspace = Workspace()
    calls = []
    marker = failure("synthetic uncertain outcome")

    class Request:
        def execute(self):
            calls.append("execute")
            raise marker

    def send(**kwargs):
        calls.append("send")
        return Request()

    workspace.gmail.users_api.messages_api.send = send
    with pytest.raises(failure) as caught:
        GmailMailboxAdapter(workspace).send_message(MailSendCommand(
            to=("synthetic@example.test",), cc=(), bcc=(), subject="Synthetic", body="Body",
        ))
    assert caught.value is marker
    assert not isinstance(caught.value, MailNotAppliedError)
    assert calls == ["send", "execute"]


@pytest.mark.parametrize("response", [{}, {"id": " "}, {"id": None}])
def test_missing_send_receipt_is_unknown_and_never_retried(response):
    workspace = Workspace()
    calls = []

    class Request:
        def execute(self):
            calls.append("execute")
            return response

    def send(**kwargs):
        calls.append("send")
        return Request()

    workspace.gmail.users_api.messages_api.send = send
    with pytest.raises(RuntimeError, match="^provider_receipt_missing$") as caught:
        GmailMailboxAdapter(workspace).send_message(MailSendCommand(
            to=("synthetic@example.test",), cc=(), bcc=(), subject="Synthetic", body="Body",
        ))
    assert not isinstance(caught.value, MailNotAppliedError)
    assert calls == ["send", "execute"]


@pytest.mark.parametrize("destination,operation", [("trash", "trash"), ("archive", "modify")])
def test_move_execute_failure_is_unknown_and_never_retried(destination, operation):
    workspace = Workspace()
    calls = []
    marker = TimeoutError("synthetic uncertain move")

    class Request:
        def execute(self):
            calls.append("execute")
            raise marker

    def move(**kwargs):
        calls.append(operation)
        return Request()

    setattr(workspace.gmail.users_api.messages_api, operation, move)
    with pytest.raises(TimeoutError) as caught:
        GmailMailboxAdapter(workspace).move_message("gm-synthetic", destination)
    assert caught.value is marker
    assert not isinstance(caught.value, MailNotAppliedError)
    assert calls == [operation, "execute"]


def test_send_request_setup_failure_never_executes_and_is_sanitized():
    workspace = Workspace()
    calls = []

    def send(**kwargs):
        calls.append("send")
        raise RuntimeError("synthetic private marker")

    workspace.gmail.users_api.messages_api.send = send
    with pytest.raises(MailNotAppliedError, match="^provider_unavailable_before_send$") as caught:
        GmailMailboxAdapter(workspace).send_message(MailSendCommand(
            to=("synthetic@example.test",), cc=(), bcc=(), subject="Synthetic", body="Body",
        ))
    assert "private" not in str(caught.value)
    assert calls == ["send"]


def test_send_missing_optional_thread_preserves_command_fallback():
    workspace = Workspace()
    calls = []

    class Request:
        def execute(self):
            calls.append("execute")
            return {"id": "gm-synthetic"}

    def send(**kwargs):
        calls.append("send")
        assert kwargs["body"]["threadId"] == "thread-synthetic"
        return Request()

    workspace.gmail.users_api.messages_api.send = send
    receipt = GmailMailboxAdapter(workspace).send_message(MailSendCommand(
        to=("synthetic@example.test",), cc=(), bcc=(), subject="Synthetic", body="Body",
        thread_id="thread-synthetic",
    ))
    assert receipt.external_message_id == "gm-synthetic"
    assert receipt.external_thread_id == "thread-synthetic"
    assert calls == ["send", "execute"]
