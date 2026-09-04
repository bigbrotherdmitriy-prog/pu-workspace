from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.integrations.contracts import MailNotAppliedError, MailSendCommand
from app.integrations.mail import mailbox_adapter_for_project
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.organization_contract import Contract
from app.models.response_draft import ResponseDraft
from app.models.user import User


router = APIRouter(prefix="/mail", tags=["mail"])
MAIL_SOURCE_TYPES = ("email", "email_outgoing")
MAIL_FOLDERS = (
    ("inbox", "Входящие"),
    ("sent", "Отправленные"),
    ("drafts", "Черновики"),
    ("attention", "Требуют внимания"),
    ("archive", "Архив"),
    ("all", "Вся почта"),
)


class MailAttachmentRef(BaseModel):
    message_id: int = Field(gt=0)
    attachment_index: int = Field(ge=0)


class MailDraftCreate(BaseModel):
    project_id: int = Field(gt=0)
    contract_id: int | None = Field(default=None, gt=0)
    mode: str = Field(default="compose", pattern="^(compose|reply|reply_all|forward)$")
    reply_to_message_id: int | None = Field(default=None, gt=0)
    to: list[str] = Field(min_length=1, max_length=100)
    cc: list[str] = Field(default_factory=list, max_length=100)
    bcc: list[str] = Field(default_factory=list, max_length=100)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    attachments: list[MailAttachmentRef] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def reply_requires_source(self):
        if self.mode != "compose" and self.reply_to_message_id is None:
            raise ValueError("reply_to_message_id is required for this mode")
        return self


class MailDraftPatch(BaseModel):
    expected_revision: int = Field(gt=0)
    contract_id: int | None = Field(default=None, gt=0)
    to: list[str] | None = Field(default=None, min_length=1, max_length=100)
    cc: list[str] | None = Field(default=None, max_length=100)
    bcc: list[str] | None = Field(default=None, max_length=100)
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    attachments: list[MailAttachmentRef] | None = Field(default=None, max_length=20)


class MailDraftApproval(BaseModel):
    revision: int = Field(gt=0)


class MailDraftSend(BaseModel):
    revision: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def _json(value: str | None, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed if fallback is None or isinstance(parsed, type(fallback)) else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _address_list(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = _json(value, None)
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [address for _, address in getaddresses([value]) if address]


def _normalize_addresses(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if "\n" in raw or "\r" in raw:
            raise HTTPException(422, "Некорректный адрес получателя")
        _, address = parseaddr(raw.strip())
        address = address.casefold()
        if not address or "@" not in address or address.startswith("@") or address.endswith("@"):
            raise HTTPException(422, "Некорректный адрес получателя")
        if address not in seen:
            seen.add(address)
            result.append(address)
    return result


def _store_to(values: list[str]) -> str:
    encoded = ", ".join(values)
    if len(encoded) > 1000:
        raise HTTPException(422, "Список основных получателей слишком длинный")
    return encoded


def _safe_headers(row: Message) -> dict:
    headers = _json(row.mail_headers_json, {})
    return {
        key: headers.get(key)
        for key in ("to", "cc", "date", "message-id", "in-reply-to", "references")
        if headers.get(key)
    }


def _message_payload(db: Session, row: Message) -> dict:
    labels = _json(row.mail_labels_json, [])
    drafts = list(db.scalars(select(ResponseDraft).where(
        ResponseDraft.message_id == row.id,
    ).order_by(ResponseDraft.id)))
    return {
        "id": row.id,
        "project_id": row.project_id,
        "contract_id": row.contract_id,
        "provider": "google_workspace" if row.source_url and "mail.google.com" in row.source_url else row.source_type,
        "direction": "outgoing" if row.source_type == "email_outgoing" else "incoming",
        "thread_id": row.source_thread_id or f"message:{row.id}",
        "subject": str(_json(row.mail_headers_json, {}).get("subject") or row.source_name),
        "sender": row.source_sender,
        "content": row.content,
        "summary": row.summary,
        "labels": labels,
        "headers": _safe_headers(row),
        "attachments": _json(row.attachments_json, []),
        "status": row.status,
        "context_confirmed": row.context_confirmed,
        "drafts": [_draft_payload(draft) for draft in drafts],
        "created_at": row.created_at,
    }


def _draft_payload(row: ResponseDraft, *, replay: bool = False) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "contract_id": row.contract_id,
        "provider": row.provider,
        "mode": row.operation_kind,
        "reply_to_message_id": row.message_id,
        "to": _address_list(row.recipient_to),
        "cc": _address_list(row.recipient_cc),
        "bcc": _address_list(row.recipient_bcc),
        "subject": row.subject,
        "body": row.body,
        "attachments": _json(row.attachments_json, []),
        "revision": row.revision,
        "approved_revision": row.approved_revision,
        "status": row.status,
        "send_attempts": row.send_attempts,
        "error_code": row.last_send_error_code,
        "receipt": ({"external_message_id": row.sent_external_id, "sent_at": row.sent_at}
                    if row.sent_external_id else None),
        "idempotent_replay": replay,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _message_rows(db: Session, project_id: int, folder: str, query: str | None, cursor: int | None):
    statement = select(Message).where(
        Message.project_id == project_id,
        Message.source_type.in_(MAIL_SOURCE_TYPES),
    )
    if cursor is not None:
        statement = statement.where(Message.id < cursor)
    rows = list(db.scalars(statement.order_by(Message.id.desc()).limit(500)))
    needle = (query or "").strip().casefold()
    result = []
    for row in rows:
        labels = set(_json(row.mail_labels_json, []))
        if folder == "inbox" and (row.source_type != "email" or (labels and "INBOX" not in labels)):
            continue
        if folder == "sent" and row.source_type != "email_outgoing":
            continue
        if folder == "drafts":
            continue
        if folder == "attention" and row.status not in {"needs_review", "needs_context_confirmation", "in_progress"}:
            continue
        if folder == "archive" and (row.source_type != "email" or not labels or "INBOX" in labels):
            continue
        if needle and needle not in f"{row.source_name}\n{row.source_sender or ''}\n{row.content}".casefold():
            continue
        result.append(row)
    return result


def _attachment_metadata(db: Session, project_id: int, refs: list[MailAttachmentRef]) -> list[dict]:
    result = []
    for ref in refs:
        message = db.get(Message, ref.message_id)
        if message is None or message.project_id != project_id or message.source_type not in MAIL_SOURCE_TYPES:
            raise HTTPException(422, "Вложение не принадлежит выбранному проекту")
        values = _json(message.attachments_json, [])
        if ref.attachment_index >= len(values):
            raise HTTPException(422, "Вложение не найдено")
        value = values[ref.attachment_index]
        result.append({
            "message_id": message.id,
            "attachment_index": ref.attachment_index,
            "name": str(value.get("name") or "attachment")[:500],
            "mime_type": str(value.get("mime_type") or "application/octet-stream")[:200],
            "size": int(value.get("size") or 0),
            "sendable": False,
        })
    return result


def _require_contract(db: Session, project_id: int, contract_id: int | None):
    if contract_id is None:
        return
    if not db.scalar(select(Contract.id).where(Contract.id == contract_id, Contract.project_id == project_id)):
        raise HTTPException(422, "Contract does not belong to this project")


@router.get("/projects/{project_id}/capabilities")
def mail_capabilities(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    adapter = mailbox_adapter_for_project(project_id, db)
    health = adapter.health()
    return {
        "provider": adapter.provider,
        "connected": health.ready,
        "features": {
            "folders": True,
            "threads": True,
            "compose": True,
            "reply": True,
            "reply_all": True,
            "forward": True,
            "cc_bcc": True,
            "attachment_metadata": True,
            "attachment_send": False,
            "explicit_revision_approval": True,
            "automatic_send": False,
        },
    }


@router.get("/projects/{project_id}/folders")
def mail_folders(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    adapter = mailbox_adapter_for_project(project_id, db)
    try:
        provider_folders = adapter.list_folders()
    except Exception as exc:
        raise HTTPException(502, "mail_provider_unavailable") from exc
    message_rows = _message_rows(db, project_id, "all", None, None)
    counts = {
        "inbox": len(_message_rows(db, project_id, "inbox", None, None)),
        "sent": len(_message_rows(db, project_id, "sent", None, None)),
        "drafts": len(list(db.scalars(select(ResponseDraft.id).where(
            ResponseDraft.project_id == project_id,
            ResponseDraft.status.in_(("draft", "approved", "failed")),
        )))),
        "attention": len(_message_rows(db, project_id, "attention", None, None)),
        "archive": len(_message_rows(db, project_id, "archive", None, None)),
        "all": len(message_rows),
    }
    return {
        "provider": adapter.provider,
        "folders": [
            *[{"id": key, "name": name, "kind": "core", "count": counts[key]} for key, name in MAIL_FOLDERS],
            *[{"id": item.id, "name": item.name, "kind": item.kind} for item in provider_folders],
        ],
    }


@router.get("/projects/{project_id}/messages")
def mail_messages(project_id: int, folder: str = "inbox", query: str | None = None,
                  limit: int = 50, cursor: int | None = None,
                  db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    if folder not in {item[0] for item in MAIL_FOLDERS}:
        raise HTTPException(422, "Unsupported mailbox folder")
    limit = max(1, min(limit, 100))
    rows = _message_rows(db, project_id, folder, query, cursor)[:limit + 1]
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "messages": [_message_payload(db, row) for row in rows],
        "next_cursor": rows[-1].id if has_more and rows else None,
        "count": len(rows),
    }


@router.get("/projects/{project_id}/threads")
def mail_threads(project_id: int, folder: str = "inbox", query: str | None = None,
                 limit: int = 50, cursor: int | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    if folder not in {item[0] for item in MAIL_FOLDERS}:
        raise HTTPException(422, "Unsupported mailbox folder")
    normalized_limit = max(1, min(limit, 100))
    grouped: dict[str, list[Message]] = {}
    for row in _message_rows(db, project_id, folder, query, cursor):
        grouped.setdefault(row.source_thread_id or f"message:{row.id}", []).append(row)
    groups = list(grouped.items())[: normalized_limit + 1]
    has_more = len(groups) > normalized_limit
    groups = groups[:normalized_limit]
    return {
        "threads": [{
            "thread_id": thread_id,
            "message_count": len(rows),
            "latest": _message_payload(db, rows[0]),
        } for thread_id, rows in groups],
        "next_cursor": groups[-1][1][0].id if has_more and groups else None,
        "count": len(groups),
    }


@router.get("/projects/{project_id}/threads/{thread_id}")
def mail_thread(project_id: int, thread_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    statement = select(Message).where(
        Message.project_id == project_id,
        Message.source_type.in_(MAIL_SOURCE_TYPES),
    )
    if thread_id.startswith("message:") and thread_id[8:].isdigit():
        statement = statement.where(
            Message.id == int(thread_id[8:]),
            Message.source_thread_id.is_(None),
        )
    else:
        statement = statement.where(Message.source_thread_id == thread_id)
    rows = list(db.scalars(statement.order_by(Message.created_at, Message.id)))
    if not rows:
        raise HTTPException(404, "Mail thread not found")
    return {"thread_id": thread_id, "messages": [_message_payload(db, row) for row in rows], "count": len(rows)}


@router.get("/messages/{message_id}")
def mail_message(message_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(Message, message_id)
    if row is None or row.source_type not in MAIL_SOURCE_TYPES:
        raise HTTPException(404, "Mail message not found")
    require_project_role(db, user, row.project_id, "viewer")
    return _message_payload(db, row)


@router.get("/projects/{project_id}/drafts")
def mail_drafts(project_id: int, status: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    statement = select(ResponseDraft).where(ResponseDraft.project_id == project_id)
    if status:
        if status not in {"draft", "approved", "rejected", "sending", "sent", "failed", "unknown"}:
            raise HTTPException(422, "Unsupported draft status")
        statement = statement.where(ResponseDraft.status == status)
    rows = list(db.scalars(statement.order_by(ResponseDraft.updated_at.desc(), ResponseDraft.id.desc()).limit(200)))
    return {"drafts": [_draft_payload(row) for row in rows], "count": len(rows)}


@router.get("/drafts/{draft_id}")
def get_mail_draft(draft_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = db.get(ResponseDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "Mail draft not found")
    require_project_role(db, user, draft.project_id, "viewer")
    return _draft_payload(draft)


@router.post("/drafts")
def create_mail_draft(payload: MailDraftCreate, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "editor")
    _require_contract(db, payload.project_id, payload.contract_id)
    source = db.get(Message, payload.reply_to_message_id) if payload.reply_to_message_id else None
    if source is not None and (source.project_id != payload.project_id or source.source_type not in MAIL_SOURCE_TYPES):
        raise HTTPException(422, "Reply source does not belong to this project")
    if payload.reply_to_message_id is not None and source is None:
        raise HTTPException(404, "Reply source not found")
    if source is not None and not source.context_confirmed:
        raise HTTPException(409, "mail_message_context_confirmation_required")
    to = _normalize_addresses(payload.to)
    cc = _normalize_addresses(payload.cc)
    bcc = _normalize_addresses(payload.bcc)
    attachment_metadata = _attachment_metadata(db, payload.project_id, payload.attachments)
    subject = payload.subject.strip()
    if payload.mode in {"reply", "reply_all"} and not subject.casefold().startswith("re:"):
        subject = f"Re: {subject}"
    if payload.mode == "forward" and not subject.casefold().startswith(("fwd:", "fw:")):
        subject = f"Fwd: {subject}"
    source_key = f"mail-draft:{uuid4()}"
    draft = ResponseDraft(
        project_id=payload.project_id,
        contract_id=payload.contract_id,
        reviewer_user_id=user.id,
        message_id=source.id if source else None,
        subject=subject,
        body=payload.body.strip(),
        recipient_to=_store_to(to),
        recipient_cc=json.dumps(cc),
        recipient_bcc=json.dumps(bcc),
        attachments_json=json.dumps(attachment_metadata, ensure_ascii=False),
        provider="google_workspace",
        operation_kind=payload.mode,
        status="draft",
        source_file_id=source_key,
        source_file_name="mail-client",
        source_excerpt="",
        source_excerpt_hash=hashlib.sha256(source_key.encode()).hexdigest(),
        confidence=1.0,
    )
    db.add(draft)
    db.flush()
    db.add(AuditLog(action="mail_draft_created", entity_type="response_draft", entity_id=draft.id,
                    details=f"project={draft.project_id}; mode={draft.operation_kind}; revision=1; attachment_refs={len(attachment_metadata)}"))
    db.commit()
    db.refresh(draft)
    return _draft_payload(draft)


@router.patch("/drafts/{draft_id}")
def update_mail_draft(draft_id: int, payload: MailDraftPatch, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = db.scalar(select(ResponseDraft).where(ResponseDraft.id == draft_id).with_for_update())
    if draft is None:
        raise HTTPException(404, "Mail draft not found")
    require_project_role(db, user, draft.project_id, "editor")
    if draft.revision != payload.expected_revision:
        raise HTTPException(409, "mail_draft_revision_conflict")
    if draft.status in {"sending", "sent", "unknown"}:
        raise HTTPException(409, "mail_draft_not_editable")
    _require_contract(db, draft.project_id, payload.contract_id)
    changed = False
    if "contract_id" in payload.model_fields_set:
        draft.contract_id = payload.contract_id
        changed = True
    for field, column in (("to", "recipient_to"), ("cc", "recipient_cc"), ("bcc", "recipient_bcc")):
        values = getattr(payload, field)
        if values is not None:
            normalized = _normalize_addresses(values)
            setattr(draft, column, _store_to(normalized) if field == "to" else json.dumps(normalized))
            changed = True
    if payload.subject is not None:
        draft.subject = payload.subject.strip()
        changed = True
    if payload.body is not None:
        draft.body = payload.body.strip()
        changed = True
    if payload.attachments is not None:
        draft.attachments_json = json.dumps(_attachment_metadata(db, draft.project_id, payload.attachments), ensure_ascii=False)
        changed = True
    if not changed:
        return _draft_payload(draft)
    draft.revision += 1
    draft.approved_revision = None
    draft.approved_by_user_id = None
    draft.approved_at = None
    draft.status = "draft"
    draft.send_idempotency_key = None
    draft.last_send_error_code = None
    db.add(AuditLog(action="mail_draft_revised", entity_type="response_draft", entity_id=draft.id,
                    details=f"project={draft.project_id}; revision={draft.revision}; approval_invalidated=true"))
    db.commit()
    db.refresh(draft)
    return _draft_payload(draft)


@router.post("/drafts/{draft_id}/approve")
def approve_mail_draft(draft_id: int, payload: MailDraftApproval, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = db.scalar(select(ResponseDraft).where(ResponseDraft.id == draft_id).with_for_update())
    if draft is None:
        raise HTTPException(404, "Mail draft not found")
    require_project_role(db, user, draft.project_id, "editor")
    if draft.revision != payload.revision:
        raise HTTPException(409, "mail_draft_revision_conflict")
    if draft.status in {"sending", "sent", "unknown"}:
        raise HTTPException(409, "mail_draft_not_approvable")
    draft.approved_revision = draft.revision
    draft.approved_by_user_id = user.id
    draft.approved_at = datetime.now(timezone.utc)
    draft.status = "approved"
    db.add(AuditLog(action="mail_draft_approved", entity_type="response_draft", entity_id=draft.id,
                    details=f"project={draft.project_id}; revision={draft.revision}; actor={user.id}"))
    db.commit()
    db.refresh(draft)
    return _draft_payload(draft)


def _idempotency_hash(draft_id: int, revision: int, key: str) -> str:
    return hashlib.sha256(f"mail-send:{draft_id}:{revision}:{key}".encode()).hexdigest()


@router.post("/drafts/{draft_id}/send")
def send_mail_draft(draft_id: int, payload: MailDraftSend, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = db.scalar(select(ResponseDraft).where(ResponseDraft.id == draft_id).with_for_update())
    if draft is None:
        raise HTTPException(404, "Mail draft not found")
    require_project_role(db, user, draft.project_id, "manager")
    key_hash = _idempotency_hash(draft.id, payload.revision, payload.idempotency_key)
    if draft.send_idempotency_key == key_hash:
        return _draft_payload(draft, replay=True)
    if draft.send_idempotency_key is not None:
        raise HTTPException(409, "mail_send_command_conflict")
    if draft.revision != payload.revision or draft.approved_revision != draft.revision or draft.status != "approved":
        raise HTTPException(409, "mail_current_revision_not_approved")
    if _json(draft.attachments_json, []):
        raise HTTPException(409, "mail_attachment_send_requires_verified_mailbox_origin")
    recipients = _address_list(draft.recipient_to)
    if not recipients:
        raise HTTPException(422, "Mail draft has no recipient")
    adapter = mailbox_adapter_for_project(draft.project_id, db)
    source = db.get(Message, draft.message_id) if draft.message_id else None
    if source is not None and source.project_id != draft.project_id:
        raise HTTPException(409, "mail_reply_origin_mismatch")
    command = MailSendCommand(
        to=recipients,
        cc=_address_list(draft.recipient_cc),
        bcc=_address_list(draft.recipient_bcc),
        subject=draft.subject,
        body=draft.body,
        thread_id=source.source_thread_id if source else None,
    )
    draft.send_idempotency_key = key_hash
    draft.status = "sending"
    draft.send_attempts += 1
    draft.last_send_at = datetime.now(timezone.utc)
    draft.last_send_error_code = None
    db.add(AuditLog(action="mail_send_started", entity_type="response_draft", entity_id=draft.id,
                    details=f"project={draft.project_id}; revision={draft.revision}; attempt={draft.send_attempts}; provider={adapter.provider}"))
    db.commit()
    try:
        receipt = adapter.send_message(command)
    except MailNotAppliedError:
        draft.status = "failed"
        draft.last_send_error_code = "provider_rejected_before_effect"
        db.add(AuditLog(action="mail_send_failed", entity_type="response_draft", entity_id=draft.id,
                        details=f"project={draft.project_id}; revision={draft.revision}; outcome=not_applied"))
        db.commit()
        return _draft_payload(draft)
    except Exception:
        draft.status = "unknown"
        draft.last_send_error_code = "provider_outcome_unknown"
        db.add(AuditLog(action="mail_send_outcome_unknown", entity_type="response_draft", entity_id=draft.id,
                        details=f"project={draft.project_id}; revision={draft.revision}; retry_blocked=true"))
        db.commit()
        return _draft_payload(draft)
    draft.sent_external_id = receipt.external_message_id
    draft.sent_at = datetime.now(timezone.utc)
    draft.status = "sent"
    db.add(AuditLog(action="mail_sent", entity_type="response_draft", entity_id=draft.id,
                    details=f"project={draft.project_id}; revision={draft.revision}; provider={adapter.provider}; receipt=true"))
    db.commit()
    db.refresh(draft)
    return _draft_payload(draft)


@router.post("/drafts/{draft_id}/retry")
def retry_mail_draft(draft_id: int, payload: MailDraftSend, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = db.get(ResponseDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "Mail draft not found")
    require_project_role(db, user, draft.project_id, "manager")
    if draft.status == "unknown":
        raise HTTPException(409, "mail_send_outcome_unknown_reconciliation_required")
    if draft.status != "failed":
        raise HTTPException(409, "mail_retry_not_available")
    draft.status = "approved"
    draft.send_idempotency_key = None
    db.commit()
    return send_mail_draft(draft_id, payload, db, user)
