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

from app.api.ai_secretary import IncomingMessage, ingest_message, project_candidate
from app.api.project_contacts import contact_for_sender, discover_contact_from_message
from app.integrations.google_workspace import google_workspace_for_project
from app.integrations.telegram import notify_telegram
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.governance import Risk
from app.models.response_draft import ResponseDraft
from app.models.task import Task
from app.models.task_completion_suggestion import TaskCompletionSuggestion
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
    task_rows = result.get("tasks", [])
    draft_rows = result.get("drafts", [])
    risk_rows = result.get("risks", [])
    lines = [
        "✉️ Новое письмо в PU Workspace",
        f"От: {sender[:180]}",
        f"Тема: {subject[:240]}",
    ]
    message_id = result.get("id")
    if message_id:
        lines.append(f"Письмо: #{message_id}")

    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.extend(("", "🧠 Анализ:", summary[:1500]))

    lines.extend((
        "",
        f"Найдено: задач {len(task_rows)} · рисков {len(risk_rows)} · черновиков {len(draft_rows)}",
    ))
    if task_rows:
        lines.append("📋 Предлагаемые задачи:")
        for task in task_rows[:3]:
            task_id = f"#{task.get('id')} · " if task.get("id") else ""
            lines.append(f"• {task_id}{str(task.get('title') or 'Без названия')[:260]}")
    if risk_rows:
        lines.append("⚠️ Риски:")
        for risk in risk_rows[:3]:
            lines.append(f"• {str(risk.get('title') or 'Без названия')[:260]}")

    if draft_rows:
        draft = draft_rows[0]
        lines.extend((
            "",
            "✍️ Черновик ответа (НЕ отправлен):",
            str(draft.get("subject") or "Ответ")[:300],
            str(draft.get("body") or "").strip()[:1500],
            "",
            "Проверьте и подтвердите отправку в разделе «Письма».",
        ))
    else:
        lines.extend(("", "Черновик ответа не создан. Проверьте письмо в разделе «Письма»."))
    return "\n".join(lines)[:4000]


def _bulk_email_reason(headers: dict[str, str], label_ids: list[str] | None, subject: str, content: str) -> str | None:
    """Return an explainable reason only for strong bulk/marketing evidence.

    Text alone is intentionally insufficient: legitimate supplier offers and
    delivery correspondence must continue through the normal review workflow.
    """
    labels = {value.upper() for value in (label_ids or [])}
    if "SPAM" in labels:
        return "Gmail пометил письмо как спам"
    if "CATEGORY_PROMOTIONS" in labels:
        return "Gmail отнёс письмо к категории «Промоакции»"
    if headers.get("list-unsubscribe"):
        return "обнаружен заголовок массовой рассылки List-Unsubscribe"
    if headers.get("list-id"):
        return "обнаружен идентификатор списка рассылки List-Id"
    precedence = headers.get("precedence", "").casefold().strip()
    if precedence in {"bulk", "list", "junk"}:
        return f"обнаружен заголовок массовой рассылки Precedence: {precedence}"
    auto_submitted = headers.get("auto-submitted", "").casefold().strip()
    if auto_submitted and auto_submitted != "no":
        text = f"{subject}\n{content}".casefold()
        marketing_markers = ("отписаться", "unsubscribe", "реклам", "рассылк", "специальное предложение")
        if any(marker in text for marker in marketing_markers):
            return "автоматическая рекламная рассылка"
    return None


def _automated_sender_reason(headers: dict[str, str]) -> str | None:
    """Explain why a message must not receive an automatic reply draft.

    The message remains visible and can still produce tasks or risks.  We only
    prevent nonsensical replies to machine-only addresses and mail systems.
    """
    sender = parseaddr(headers.get("from", ""))[1].casefold()
    local_part = sender.partition("@")[0]
    auto_submitted = headers.get("auto-submitted", "").casefold().strip()
    if auto_submitted and auto_submitted != "no":
        return "автоматическое служебное письмо"
    if headers.get("x-auto-response-suppress"):
        return "отправитель запретил автоматические ответы"
    machine_addresses = ("noreply", "no-reply", "do-not-reply", "donotreply")
    if any(marker in local_part for marker in machine_addresses):
        return "адрес отправителя не принимает ответы"
    return None


def _apply_bulk_filter(message: Message, reason: str | None) -> bool:
    """Backfill safe filtering without overriding a human workflow decision."""
    if not reason or message.status not in {"needs_review", "needs_context_confirmation", "ready"}:
        return False
    message.status = "filtered"
    message.summary = f"Автоматические действия не создавались: {reason}."
    return True


def _apply_automated_filter(message: Message, reason: str | None, *, has_actions: bool) -> bool:
    """Keep actionable machine mail, but remove non-actionable noise from attention."""
    if not reason or has_actions or message.status not in {"needs_review", "needs_context_confirmation", "ready"}:
        return False
    message.status = "filtered"
    message.summary = f"Служебное письмо без действий: {reason}."
    return True


@router.post("/projects/{project_id}/gmail/sync")
def sync_gmail(project_id: int, payload: GmailSyncRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    return sync_gmail_project(project_id, db, user, query=payload.query, max_results=payload.max_results)


def sync_gmail_project(project_id: int, db: Session, user: User, *, query: str, max_results: int) -> dict:
    service = google_workspace_for_project(project_id, db).service("gmail", "v1")
    page = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    processed = skipped = failed = 0
    errors: list[dict] = []
    notified_threads: set[str] = set()
    for ref in page.get("messages", []):
        try:
            item = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            headers = _headers(item.get("payload", {}))
            content = _message_text(item.get("payload", {})) or item.get("snippet", "")
            subject = headers.get("subject") or "Письмо без темы"
            sender = headers.get("from") or "Отправитель не указан"
            recipient = headers.get("to") or "Получатель не указан"
            is_outgoing = "SENT" in set(item.get("labelIds") or [])
            source_type = "email_outgoing" if is_outgoing else "email"
            correspondent = recipient if is_outgoing else sender
            bulk_reason = None if is_outgoing else _bulk_email_reason(
                headers, item.get("labelIds"), subject, content,
            )
            automated_sender_reason = None if is_outgoing else _automated_sender_reason(headers)
            existing = db.scalar(select(Message).where(
                Message.source_external_id == ref["id"],
            ))
            if existing:
                _apply_bulk_filter(existing, bulk_reason)
                has_actions = bool(
                    db.scalar(select(Task.id).where(Task.message_id == existing.id))
                    or db.scalar(select(Risk.id).where(
                        Risk.project_id == existing.project_id,
                        Risk.source_id == f"message:{existing.id}",
                    ))
                    or db.scalar(select(TaskCompletionSuggestion.id).where(
                        TaskCompletionSuggestion.message_id == existing.id,
                    ))
                )
                _apply_automated_filter(existing, automated_sender_reason, has_actions=has_actions)
                # Older synchronized rows predate attachment metadata. Backfill
                # metadata once, without re-running message analysis or alerts.
                existing_attachments = json.loads(existing.attachments_json or "[]")
                if not existing_attachments or any(not value.get("document_external_id") for value in existing_attachments):
                    existing.attachments_json = json.dumps(_attachments(item.get("payload", {}), item["id"]), ensure_ascii=False)
                if existing.source_sender and not bulk_reason:
                    discover_contact_from_message(db, existing.project_id, existing.source_sender, existing.content, user)
                # Older messages may have been synchronized before email fallback
                # drafts existed. Backfill a reviewable draft without sending it
                # and without changing the message's confirmed project context.
                if existing.source_type == "email" and not bulk_reason and not automated_sender_reason and existing.status != "filtered" and not db.scalar(select(ResponseDraft.id).where(
                    ResponseDraft.message_id == existing.id,
                )):
                    synthetic = StorageObject(
                        id=f"message:{existing.id}", name=existing.source_name,
                        mime_type="text/plain", parent_id="ai-secretary", content_text=existing.content,
                    )
                    drafts = create_response_drafts(
                        db, existing.project_id, None, [synthetic], ensure_response=True,
                    )
                    for draft in drafts:
                        draft.message_id = existing.id
                skipped += 1
                continue
            attachments = _attachments(item.get("payload", {}), item["id"])
            if not content.strip():
                skipped += 1
                continue
            contact = contact_for_sender(db, project_id, correspondent, user)
            if contact is not None:
                target_project_id = contact.project_id
                routing_evidence = f"Проект определён по email клиента: {contact.email}"
                routing_contract_id = contact.contract_id
            else:
                target_project_id, _, _ = project_candidate(
                    db, project_id, f"{subject}\n{correspondent}\n{content}", user,
                )
                routing_evidence = None
                routing_contract_id = None
            result = ingest_message(IncomingMessage(
                project_id=target_project_id, source_type=source_type, source_external_id=item["id"],
                source_name=(f"Исходящее: {recipient} — {subject}" if is_outgoing else f"{sender} — {subject}"),
                source_url=f"https://mail.google.com/mail/u/0/#all/{item['id']}",
                source_sender=correspondent, source_thread_id=item.get("threadId"), content=content,
                attachments=attachments,
                routing_contract_id=routing_contract_id, routing_evidence=routing_evidence,
                automation_suppressed=bool(bulk_reason),
                automation_suppression_reason=bulk_reason,
                response_suppressed=bool(automated_sender_reason),
                response_suppression_reason=automated_sender_reason,
            ), db, user)
            processed += 1 if result["status"] else 0
            if not bulk_reason and result["status"] != "filtered":
                discover_contact_from_message(db, target_project_id, correspondent, content, user)
            thread_key = item.get("threadId") or item["id"]
            if not is_outgoing and not bulk_reason and thread_key not in notified_threads:
                notify_telegram(_gmail_telegram_notice(sender, subject, result))
                notified_threads.add(thread_key)
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
    recipient = draft.recipient_to or (
        parseaddr(source.source_sender)[1]
        if source is not None and source.source_type == "email" and source.source_sender else ""
    )
    if not recipient:
        raise HTTPException(422, "Не удалось определить адрес получателя")
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = (
        draft.subject if draft.recipient_to or draft.subject.lower().startswith("re:")
        else f"Re: {draft.subject}"
    )
    message.set_content(draft.body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    service = google_workspace_for_project(draft.project_id, db).service("gmail", "v1")
    body = {"raw": raw}
    if source is not None and source.source_thread_id:
        body["threadId"] = source.source_thread_id
    sent = service.users().messages().send(userId="me", body=body).execute()
    draft.sent_external_id = sent["id"]
    draft.sent_at = datetime.now(timezone.utc)
    draft.status = "sent"
    db.add(AuditLog(action="gmail_reply_sent", entity_type="response_draft", entity_id=draft.id,
                    details=f"message={source.id if source else 'proactive'}; gmail_message={sent['id']}"))
    db.commit()
    return {"id": draft.id, "status": draft.status, "gmail_message_id": draft.sent_external_id, "already_sent": False}
