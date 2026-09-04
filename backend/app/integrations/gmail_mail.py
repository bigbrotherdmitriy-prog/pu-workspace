from __future__ import annotations

import base64
from email.message import EmailMessage

from app.integrations.contracts import (
    AdapterHealth,
    MailFolder,
    MailNotAppliedError,
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
