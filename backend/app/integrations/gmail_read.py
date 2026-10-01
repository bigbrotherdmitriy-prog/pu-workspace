"""Ordinary project Gmail reads, deliberately separate from Product AUTO.

Signed OAuth ownership + project RBAC authorize list/get only. This capability
does not grant mailbox origin, authority, rollout, provider actions or AUTO.
"""
from dataclasses import dataclass
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import MailboxCredentialGeneration
from app.models.project import Project
from app.models.v54_pilot import ConnectionIdentity, MailConnection

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
RECEIPT_PREFIX = "gmail-read:"
AUDIT_ACTION = "gmail_project_read"


@dataclass(frozen=True)
class ProjectGmailReadConnection:
    project_id: int
    organization_id: int
    token_id: int
    token_generation: int
    credential_receipt_id: str
    identity_id: str
    binding_epoch: int
    mail_connection_id: str

    def audit_pin(self):
        return dict(token_id=self.token_id, token_generation=self.token_generation,
                    credential_receipt_id=self.credential_receipt_id,
                    binding_epoch=self.binding_epoch)


def resolve_project_gmail_read(db, project_id: int, *, lock=False):
    def one(query):
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return db.scalar(query)

    project = one(select(Project).where(Project.id == project_id))
    if project is None or project.archived_at is not None:
        raise HTTPException(409, "gmail_project_unavailable")
    token = one(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project_id))
    if token is None or not token.access_token:
        raise HTTPException(409, "gmail_authorization_required")
    if READ_SCOPE not in (token.scopes or "").split():
        raise HTTPException(409, "gmail_read_scope_required")
    # Latest signed-sub receipt belonging to THIS project's token, never the
    # account-wide current generation (another project can rotate that pointer).
    receipt = one(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.google_token_id == token.id,
    ).order_by(MailboxCredentialGeneration.verified_at.desc(),
               MailboxCredentialGeneration.generation.desc()).limit(1))
    if (receipt is None or receipt.organization_id != project.organization_id
            or receipt.state != "active" or receipt.verified_at is None):
        raise HTTPException(409, "gmail_verified_connection_required")
    identity = one(select(ConnectionIdentity).where(ConnectionIdentity.id == receipt.connection_identity_id))
    if (identity is None or identity.organization_id != project.organization_id
            or identity.provider != "google_workspace" or identity.state != "verified"
            or identity.verified_at is None or identity.binding_epoch != receipt.binding_epoch):
        raise HTTPException(409, "gmail_connection_revoked")
    mail = one(select(MailConnection).where(
        MailConnection.identity_id == identity.id, MailConnection.namespace == "gmail"))
    if mail is None or mail.organization_id != project.organization_id or mail.state != "active":
        raise HTTPException(409, "gmail_connection_revoked")
    return ProjectGmailReadConnection(project.id, project.organization_id, token.id,
        token.credential_generation, receipt.id, identity.id, identity.binding_epoch, mail.id)


def record_read_result(db, connection, *, code: str, counts=None):
    db.add(AuditLog(action=AUDIT_ACTION, entity_type="project", entity_id=connection.project_id,
        details=json.dumps({**connection.audit_pin(), "code": code, "counts": counts or {}}, sort_keys=True)))


def project_gmail_read_status(db, project_id: int):
    try:
        connection = resolve_project_gmail_read(db, project_id)
    except HTTPException as exc:
        return {"available": False, "connected": False, "state": exc.detail,
                "label": "Нужно переподключить", "detail": exc.detail}
    last = db.scalar(select(AuditLog).where(
        AuditLog.action == AUDIT_ACTION, AuditLog.entity_type == "project",
        AuditLog.entity_id == project_id).order_by(AuditLog.id.desc()).limit(1))
    try:
        result = json.loads(last.details or "{}") if last else {}
    except (ValueError, TypeError):
        result = {}
    current = all(result.get(key) == value for key, value in connection.audit_pin().items())
    code = result.get("code") if current else None
    good = code == "ok"
    unavailable = code in {"gmail_provider_authorization_failed", "gmail_provider_scope_denied"}
    return {"available": not unavailable, "connected": good,
            "state": "verified" if good else code or "configured_not_checked",
            "label": "Чтение проверено" if good else "Ошибка чтения" if code else "Нужно проверить",
            "detail": ("Только чтение; без AUTO-действий" if good else
                       code or "OAuth подключён; получение писем ещё не проверено")}
