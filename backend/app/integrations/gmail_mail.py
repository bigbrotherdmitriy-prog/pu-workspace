from __future__ import annotations

import base64
from email.message import EmailMessage

from app.integrations.contracts import (
    AdapterHealth,
    MailFolder,
    MailNotAppliedError,
    MailMoveReceipt,
    MailSendCommand,
    MailSendReceipt,
)


class GmailMailboxAdapter:
    """Gmail implementation behind the provider-neutral mailbox contract."""

    provider = "google_workspace"

    def __init__(self, workspace_adapter):
        self._workspace = workspace_adapter

    def health(self) -> AdapterHealth:
        return self._workspace.health()

    def capabilities(self) -> dict[str, bool]:
        scopes = self._workspace.authorized_scopes()
        can_read = bool({
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
        }.intersection(scopes))
        can_send = "https://www.googleapis.com/auth/gmail.send" in scopes
        can_move = "https://www.googleapis.com/auth/gmail.modify" in scopes
        return {
            "folders": can_read,
            "threads": can_read,
            "compose": can_send,
            "reply": can_send,
            "reply_all": can_send,
            "forward": can_send,
            "cc_bcc": can_send,
            "attachment_metadata": can_read,
            "attachment_send": False,
            "move": can_move,
            "explicit_revision_approval": True,
            "automatic_send": False,
        }

    def _service(self):
        return self._workspace.service("gmail", "v1")

    def list_folders(self) -> list[MailFolder]:
        response = self._service().users().labels().list(userId="me").execute()
        result = []
        for item in response.get("labels", []):
            label_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if label_id and name:
                result.append(MailFolder(id=label_id, name=name, kind=str(item.get("type") or "label").lower()))
        return result

    def send_message(self, command: MailSendCommand) -> MailSendReceipt:
        message = EmailMessage()
        message["To"] = ", ".join(command.to)
        if command.cc:
            message["Cc"] = ", ".join(command.cc)
        if command.bcc:
            message["Bcc"] = ", ".join(command.bcc)
        message["Subject"] = command.subject
        message.set_content(command.body)
        if command.html_body:
            message.add_alternative(command.html_body, subtype="html")
        body: dict[str, str] = {
            "raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii"),
        }
        if command.thread_id:
            body["threadId"] = command.thread_id
        try:
            request = self._service().users().messages().send(userId="me", body=body)
        except Exception:
            # No provider request has been executed, so a human-approved retry
            # can safely use a new idempotency command.
            raise MailNotAppliedError("provider_unavailable_before_send") from None
        sent = request.execute()
        external_id = str(sent.get("id") or "").strip()
        if not external_id:
            raise RuntimeError("provider_receipt_missing")
        return MailSendReceipt(
            external_message_id=external_id,
            external_thread_id=str(sent.get("threadId") or "").strip() or command.thread_id,
        )

    def move_message(self, external_message_id: str, destination: str) -> MailMoveReceipt:
        if not self.capabilities()["move"]:
            raise MailNotAppliedError("gmail_modify_scope_required")
        service = self._service().users().messages()
        try:
            if destination == "trash":
                request = service.trash(userId="me", id=external_message_id)
            else:
                labels = {
                    "archive": {"addLabelIds": [], "removeLabelIds": ["INBOX"]},
                    "spam": {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]},
                    "inbox": {"addLabelIds": ["INBOX"], "removeLabelIds": ["SPAM", "TRASH"]},
                }.get(destination)
                if labels is None:
                    raise MailNotAppliedError("unsupported_mail_destination")
                request = service.modify(userId="me", id=external_message_id, body=labels)
        except MailNotAppliedError:
            raise
        except Exception:
            raise MailNotAppliedError("provider_unavailable_before_move") from None
        request.execute()
        return MailMoveReceipt(external_message_id=external_message_id, destination=destination)
