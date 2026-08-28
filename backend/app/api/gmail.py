from __future__ import annotations

import base64
import html
import json
import os
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.ai_secretary import IncomingMessage, ingest_message
from app.integrations.google_workspace import google_workspace_for_project
from app.integrations.telegram import notify_telegram
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.core.integration_types import StorageObject
from app.document_engine import index_documents
from app.governance_engine import create_governance_items
from app.organizer_engine.content import extract_text
from app.response_engine import create_response_drafts
from app.task_engine import create_tasks_from_files

router = APIRouter(tags=["gmail"])
MAX_ATTACHMENT_BYTES = int(os.getenv("GMAIL_ATTACHMENT_MAX_BYTES", str(10 * 1024 * 1024)))


class GmailSyncRequest(BaseModel):
    query: str = Field(default="newer_than:7d", min_length=1, max_length=500)
    max_results: int = Field(default=25, ge=1, le=100)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8", errors="replace")


def _message_text(payload: dict) -> str:
    candidates: list[tuple[str, str]] = []

    def walk(part: dict):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            candidates.append((mime, _decode(data)))
        for child in part.get("parts", []):
            walk(child)

    walk(payload)
    plain = next((text for mime, text in candidates if mime == "text/plain"), "")
    if plain:
        return plain.strip()
    markup = next((text for mime, text in candidates if mime == "text/html"), "")
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))).strip()


def _headers(payload: dict) -> dict[str, str]:
    return {item.get("name", "").lower(): item.get("value", "") for item in payload.get("headers", [])}


def _attachments(payload: dict, message_external_id: str | None = None) -> list[dict]:
    result: list[dict] = []
    def walk(part: dict):
        filename = (part.get("filename") or "").strip()
        body = part.get("body", {})
        if filename:
            attachment_id = body.get("attachmentId") or ""
            result.append({
                "name": filename[:500],
                "mime_type": (part.get("mimeType") or "application/octet-stream")[:200],
                "size": int(body.get("size") or 0),
                "attachment_id": attachment_id,
                "document_external_id": (
                    f"gmail:{message_external_id}:{attachment_id}"
                    if message_external_id and attachment_id else ""
                ),
            })
        for child in part.get("parts", []):
            walk(child)
    walk(payload)
    return result[:100]


def _gmail_telegram_notice(sender: str, subject: str, result: dict) -> str:
    tasks = len(result.get("tasks", []))
    drafts = len(result.get("drafts", []))
    risks = len(result.get("risks", []))
    return (
        "✉️ Новое письмо в PU Workspace\n"
        f"От: {sender[:180]}\n"
        f"Тема: {subject[:240]}\n"
        f"Найдено: задач {tasks} · рисков {risks} · черновиков {drafts}\n"
        "Откройте раздел «Письма» для проверки."
    )


@router.post("/projects/{project_id}/gmail/sync")
def sync_gmail(project_id: int, payload: GmailSyncRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    return sync_gmail_project(project_id, db, user, query=payload.query, max_results=payload.max_results)


def sync_gmail_project(project_id: int, db: Session, user: User, *, query: str, max_results: int) -> dict:
    service = google_workspace_for_project(project_id, db).service("gmail", "v1")
    page = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    processed = skipped = failed = 0
    errors: list[dict] = []
    for ref in page.get("messages", []):
        try:
            existing = db.scalar(select(Message).where(
                Message.source_type == "email",
                Message.source_external_id == ref["id"],
            ))
            if existing:
                # Older synchronized rows predate attachment metadata. Backfill
                # metadata once, without re-running message analysis or alerts.
                existing_attachments = json.loads(existing.attachments_json or "[]")
                if not existing_attachments or any(not value.get("document_external_id") for value in existing_attachments):
                    item = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
                    existing.attachments_json = json.dumps(_attachments(item.get("payload", {}), item["id"]), ensure_ascii=False)
                skipped += 1
                continue
            item = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            headers = _headers(item.get("payload", {}))
            content = _message_text(item.get("payload", {})) or item.get("snippet", "")
            attachments = _attachments(item.get("payload", {}), item["id"])
            if not content.strip():
                skipped += 1
                continue
            subject = headers.get("subject") or "Письмо без темы"
            sender = headers.get("from") or "Отправитель не указан"
            result = ingest_message(IncomingMessage(
                project_id=project_id, source_type="email", source_external_id=item["id"],
                source_name=f"{sender} — {subject}", source_url=f"https://mail.google.com/mail/u/0/#inbox/{item['id']}",
                source_sender=sender, source_thread_id=item.get("threadId"), content=content,
                attachments=attachments,
            ), db, user)
            processed += 1 if result["status"] else 0
            notify_telegram(_gmail_telegram_notice(sender, subject, result))
        except Exception as exc:
            db.rollback()
            failed += 1
            errors.append({"message_id": ref.get("id"), "error": exc.__class__.__name__})
    db.add(AuditLog(action="gmail_sync", entity_type="project", entity_id=project_id,
                    details=f"query={query}; processed={processed}; skipped={skipped}; failed={failed}"))
    db.commit()
    return {"processed": processed, "skipped": skipped, "failed": failed, "errors": errors[:20]}


@router.post("/ai-secretary/inbox/{message_id}/attachments/{attachment_index}/import")
def import_gmail_attachment(message_id: int, attachment_index: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    source = db.get(Message, message_id)
    if source is None or source.source_type != "email":
        raise HTTPException(404, "Email message not found")
    require_project_role(db, user, source.project_id, "editor")
    attachments = json.loads(source.attachments_json or "[]")
    if attachment_index < 0 or attachment_index >= len(attachments):
        raise HTTPException(404, "Attachment not found")
    metadata = attachments[attachment_index]
    attachment_id = metadata.get("attachment_id")
    if not attachment_id:
        raise HTTPException(422, "Attachment cannot be downloaded")
    if int(metadata.get("size") or 0) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, f"Attachment exceeds {MAX_ATTACHMENT_BYTES // 1024 // 1024} MB")
    external_id = metadata.get("document_external_id") or f"gmail:{source.source_external_id}:{attachment_id}"
    existing_document = db.scalar(select(Document).where(
        Document.project_id == source.project_id,
        Document.external_id == external_id,
    ))
    if existing_document:
        return {"document_id": existing_document.id, "name": existing_document.name, "tasks": 0, "drafts": 0,
                "risks": 0, "decisions": 0, "already_indexed": True}
    service = google_workspace_for_project(source.project_id, db).service("gmail", "v1")
    payload = service.users().messages().attachments().get(
        userId="me", messageId=source.source_external_id, id=attachment_id,
    ).execute()
    data = base64.urlsafe_b64decode(payload.get("data", "") + "=" * (-len(payload.get("data", "")) % 4))
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, f"Attachment exceeds {MAX_ATTACHMENT_BYTES // 1024 // 1024} MB")
    name = metadata.get("name") or "attachment"
    mime_type = metadata.get("mime_type") or "application/octet-stream"
    content = extract_text(data, mime_type, name)
    if not content:
        raise HTTPException(422, "Текст из вложения не извлечён; возможно, требуется OCR")
    item = StorageObject(
        id=external_id, name=name,
        mime_type=mime_type, parent_id=f"message:{source.id}", size=len(data), content_text=content,
    )
    documents = index_documents(db, source.project_id, [item], "gmail")
    tasks = create_tasks_from_files(db, source.project_id, source.contract_id, [item], source_type="email_attachment")
    drafts = create_response_drafts(db, source.project_id, source.contract_id, [item])
    risks, decisions = create_governance_items(db, source.project_id, [item], source_type="email_attachment")
    db.add(AuditLog(action="gmail_attachment_imported", entity_type="message", entity_id=source.id,
                    details=f"name={name}; documents={len(documents)}; tasks={len(tasks)}; risks={len(risks)}"))
    db.commit()
    return {"document_id": documents[0].id, "name": name, "tasks": len(tasks), "drafts": len(drafts),
            "risks": len(risks), "decisions": len(decisions), "already_indexed": False}


@router.post("/response-drafts/{draft_id}/send-gmail")
def send_gmail(draft_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = db.get(ResponseDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "Response draft not found")
    require_project_role(db, user, draft.project_id, "manager")
    if draft.sent_external_id:
        return {"id": draft.id, "status": "sent", "gmail_message_id": draft.sent_external_id, "already_sent": True}
    if draft.status != "approved":
        raise HTTPException(409, "Сначала подтвердите и при необходимости отредактируйте проект ответа")
    source = db.get(Message, draft.message_id) if draft.message_id else None
    if source is None or source.source_type != "email" or not source.source_sender:
        raise HTTPException(422, "Черновик не связан с письмом Gmail")
    recipient = parseaddr(source.source_sender)[1]
    if not recipient:
        raise HTTPException(422, "Не удалось определить адрес получателя")
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = draft.subject if draft.subject.lower().startswith("re:") else f"Re: {draft.subject}"
    message.set_content(draft.body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    service = google_workspace_for_project(draft.project_id, db).service("gmail", "v1")
    body = {"raw": raw}
    if source.source_thread_id:
        body["threadId"] = source.source_thread_id
    sent = service.users().messages().send(userId="me", body=body).execute()
    draft.sent_external_id = sent["id"]
    draft.sent_at = datetime.now(timezone.utc)
    draft.status = "sent"
    db.add(AuditLog(action="gmail_reply_sent", entity_type="response_draft", entity_id=draft.id,
                    details=f"message={source.id}; gmail_message={sent['id']}"))
    db.commit()
    return {"id": draft.id, "status": draft.status, "gmail_message_id": draft.sent_external_id, "already_sent": False}
