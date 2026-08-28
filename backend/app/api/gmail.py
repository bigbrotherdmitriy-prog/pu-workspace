from __future__ import annotations

import base64
import html
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
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.response_draft import ResponseDraft
from app.models.user import User

router = APIRouter(tags=["gmail"])


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


@router.post("/projects/{project_id}/gmail/sync")
def sync_gmail(project_id: int, payload: GmailSyncRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    service = google_workspace_for_project(project_id, db).service("gmail", "v1")
    page = service.users().messages().list(userId="me", q=payload.query, maxResults=payload.max_results).execute()
    processed = skipped = failed = 0
    errors: list[dict] = []
    for ref in page.get("messages", []):
        try:
            existing = db.scalar(select(Message.id).where(
                Message.source_type == "email",
                Message.source_external_id == ref["id"],
            ))
            if existing:
                skipped += 1
                continue
            item = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            headers = _headers(item.get("payload", {}))
            content = _message_text(item.get("payload", {})) or item.get("snippet", "")
            if not content.strip():
                skipped += 1
                continue
            subject = headers.get("subject") or "Письмо без темы"
            sender = headers.get("from") or "Отправитель не указан"
            result = ingest_message(IncomingMessage(
                project_id=project_id, source_type="email", source_external_id=item["id"],
                source_name=f"{sender} — {subject}", source_url=f"https://mail.google.com/mail/u/0/#inbox/{item['id']}",
                source_sender=sender, source_thread_id=item.get("threadId"), content=content,
            ), db, user)
            processed += 1 if result["status"] else 0
        except Exception as exc:
            db.rollback()
            failed += 1
            errors.append({"message_id": ref.get("id"), "error": exc.__class__.__name__})
    db.add(AuditLog(action="gmail_sync", entity_type="project", entity_id=project_id,
                    details=f"query={payload.query}; processed={processed}; skipped={skipped}; failed={failed}"))
    db.commit()
    return {"processed": processed, "skipped": skipped, "failed": failed, "errors": errors[:20]}


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
