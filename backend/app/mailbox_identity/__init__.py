"""Mailbox identity foundation; disabled until scoped database flags enable it."""

from app.mailbox_identity.service import MailboxIdentityService, MailboxConflict

__all__ = ["MailboxIdentityService", "MailboxConflict"]
