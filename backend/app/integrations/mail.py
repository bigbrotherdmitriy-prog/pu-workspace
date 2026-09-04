from __future__ import annotations

from sqlalchemy.orm import Session

from app.integrations.contracts import MailboxAdapter
from app.integrations.gmail_mail import GmailMailboxAdapter
from app.integrations.google_workspace import google_workspace_for_project


def mailbox_adapter_for_project(project_id: int, db: Session) -> MailboxAdapter:
    """Resolve a mailbox outside Core; Gmail is the first replaceable provider."""

    return GmailMailboxAdapter(google_workspace_for_project(project_id, db))
