from __future__ import annotations

import base64
import html
import json
import os
import re
from email.utils import parseaddr
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.ai_secretary import IncomingMessage, ingest_message, project_candidate
from app.api.project_contacts import contact_for_sender, discover_contact_from_message
from app.integrations.google_workspace import google_workspace_for_project
from app.integrations.google_workspace import google_workspace_for_mailbox
from app.integrations.google_retry import GoogleReadError, execute_google_read
from app.mailbox_identity.runtime import (
    observe_gmail_message,
    require_mailbox_authority,
    runtime_for_message,
    runtime_for_project_connection,
)
from app.integrations.telegram import notify_telegram
from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.models.project import Project
from app.core.integration_types import StorageObject
from app.response_engine import create_response_drafts
from app.staging.gmail import (
    GmailAttachmentBinding,
    GmailAttachmentDenied,
    GmailAttachmentIntegrityError,
    GmailAttachmentUnavailable,
    GmailProviderDownloadAdapter,
    attachment_declaration,
    enqueue_staged_gmail_attachment,
    stage_gmail_attachment,
)
from app.provider_actions.contracts import ProviderActionError
from app.provider_actions.product import queue_confirmed_action

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


def _gmail_message_refs(
    service,
    *,
    query: str,
    max_results: int,
    before_attempt: Callable[[], None] | None = None,
):
    """Yield a bounded Gmail listing across opaque provider pages.

    Gmail may return fewer rows than ``maxResults`` while still providing a
    ``nextPageToken``.  The API contract treats ``max_results`` as a total
    synchronization budget, not as a per-page value.  Repeated or malformed
    cursors fail closed instead of allowing an unbounded provider loop.
    """
    remaining = max_results
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while remaining > 0:
        request = {"userId": "me", "q": query, "maxResults": remaining}
        if page_token is not None:
            request["pageToken"] = page_token
        page = execute_google_read(
            lambda: service.users().messages().list(**request),
            before_attempt=before_attempt,
        )
        if not isinstance(page, dict):
            raise ValueError("gmail_page_unavailable")
        refs = page.get("messages") or []
        if not isinstance(refs, list):
            raise ValueError("gmail_page_unavailable")
        batch = refs[:remaining]
        yield from batch
        remaining -= len(batch)
        next_token = page.get("nextPageToken")
        if next_token is None or remaining == 0:
            return
        if not batch:
            raise ValueError("gmail_page_unavailable")
        if (not isinstance(next_token, str) or not next_token
                or len(next_token) > 2000 or next_token in seen_tokens):
            raise ValueError("gmail_page_unavailable")
        seen_tokens.add(next_token)
        page_token = next_token


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


@router.post("/projects/{project_id}/gmail/sync")
def sync_gmail(project_id: int, payload: GmailSyncRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "editor")
    try:
        return sync_gmail_project(
            project_id, db, user,
            query=payload.query, max_results=payload.max_results,
        )
    except GoogleReadError as exc:
        raise HTTPException(503, "Gmail synchronization is temporarily unavailable") from exc


def sync_gmail_project(project_id: int, db: Session, user: User, *, query: str, max_results: int) -> dict:
    require_project_role(db, user, project_id, "editor")
    sync_project = db.get(Project, project_id)
    if sync_project is None:
        raise HTTPException(404, "Project not found")
    try:
        mailbox_runtime = runtime_for_project_connection(db, project_id)
    except ValueError as exc:
        raise HTTPException(409, "Mailbox identity is unavailable") from exc
    if mailbox_runtime and mailbox_runtime.mailbox_cohort and not mailbox_runtime.flags.pilot_write:
        raise HTTPException(409, "Mailbox cohort requires an explicit current-generation write flag")
    service = (google_workspace_for_mailbox(mailbox_runtime.google_token_id, db)
               if mailbox_runtime else google_workspace_for_project(project_id, db)).service("gmail", "v1")
    def before_provider_read():
        if mailbox_runtime is None:
            return
        try:
            current = runtime_for_project_connection(db, project_id)
        except ValueError:
            current = None
        if (current is None or current.organization_id != mailbox_runtime.organization_id
                or current.identity_id != mailbox_runtime.identity_id
                or current.mail_connection_id != mailbox_runtime.mail_connection_id
                or current.generation != mailbox_runtime.generation
                or current.binding_epoch != mailbox_runtime.binding_epoch
                or current.google_token_id != mailbox_runtime.google_token_id
                or not current.flags.pilot_write):
            raise GoogleReadError("mailbox_generation_changed", retryable=False)
    processed = skipped = failed = 0
    errors: list[dict] = []
    for ref in _gmail_message_refs(
        service, query=query, max_results=max_results,
        before_attempt=before_provider_read,
    ):
        try:
            item = execute_google_read(lambda: service.users().messages().get(
                userId="me", id=ref["id"], format="full",
            ), before_attempt=before_provider_read)
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
            mailbox_write = bool(mailbox_runtime and mailbox_runtime.flags.pilot_write)
            existing = db.scalar(select(Message).where(
                Message.mail_connection_id == mailbox_runtime.mail_connection_id,
                Message.provider_message_id == ref["id"],
            )) if mailbox_write else db.scalar(select(Message).where(
                Message.mail_connection_id.is_(None), Message.source_external_id == ref["id"],
                Message.source_type.in_(("email", "email_outgoing")),
            ))
            if existing:
                require_project_role(db, user, existing.project_id, "editor")
                if existing.organization_id != sync_project.organization_id:
                    raise HTTPException(409, "Message identity requires mailbox-scoped reconciliation")
                if mailbox_write:
                    source_ref, observation = observe_gmail_message(
                        db, runtime=mailbox_runtime, project_id=existing.project_id,
                        provider_message_id=item["id"], provider_thread_id=item.get("threadId"),
                        observation_key=str(item.get("historyId") or item["id"]),
                    )
                    from app.mailbox_identity.service import MailboxIdentityService
                    MailboxIdentityService().record_provider_observed_origin(
                        db, message=existing, runtime=mailbox_runtime, source=source_ref,
                        source_version=observation, actor=user)
                # Older synchronized rows predate attachment metadata. Backfill
                # metadata once, without re-running message analysis or alerts.
                existing_attachments = json.loads(existing.attachments_json or "[]")
                if not existing_attachments or any(not value.get("document_external_id") for value in existing_attachments):
                    existing.attachments_json = json.dumps(_attachments(item.get("payload", {}), item["id"]), ensure_ascii=False)
                if existing.source_sender and not bulk_reason and existing.context_confirmed:
                    discover_contact_from_message(
                        db, existing.project_id, existing.source_sender, existing.content, user,
                        mail_connection_id=(mailbox_runtime.mail_connection_id if mailbox_write else None),
                        source_message_id=existing.id,
                    )
                # Older messages may have been synchronized before email fallback
                # drafts existed. Backfill a reviewable draft without sending it
                # and without changing the message's confirmed project context.
                if (existing.source_type == "email"
                        and not bulk_reason and existing.status != "filtered"
                        and existing.context_confirmed
                        and not db.scalar(select(ResponseDraft.id).where(
                    ResponseDraft.message_id == existing.id,
                ))):
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
            target_project_id, routing_confidence, semantic_evidence = project_candidate(
                db, project_id, f"{subject}\n{content}", user,
            )
            contact = contact_for_sender(
                db, project_id, correspondent, user,
                mail_connection_id=(mailbox_runtime.mail_connection_id if mailbox_write else None),
            )
            routing_evidence = None
            routing_contract_id = None
            if contact is not None and (routing_confidence == 0.40 or
                                       (routing_confidence >= 0.90 and target_project_id != contact.project_id)):
                routing_confidence = 0.40
                routing_evidence = (f"Конфликт email и содержания; кандидаты проектов: {contact.project_id},{target_project_id}; "
                                    f"{semantic_evidence}; требуется подтверждение")[:1000]
            elif contact is not None:
                target_project_id = contact.project_id
                routing_confidence = 0.99
                routing_evidence = f"Проект определён по email клиента: {contact.email}"
                routing_contract_id = contact.contract_id
            else:
                routing_evidence = semantic_evidence
            ingress_origin = None
            if mailbox_write:
                source_ref, _observation = observe_gmail_message(
                    db, runtime=mailbox_runtime, project_id=target_project_id,
                    provider_message_id=item["id"],
                    provider_thread_id=item.get("threadId"),
                    observation_key=str(item.get("historyId") or item["id"]),
                )
                ingress_origin = type("IngressOrigin", (), {
                    "mail_connection_id": mailbox_runtime.mail_connection_id,
                    "source_reference_id": source_ref.id,
                    "runtime": mailbox_runtime,
                    "source": source_ref,
                    "source_version": _observation,
                })()
            result = ingest_message(IncomingMessage(
                project_id=target_project_id, source_type=source_type, source_external_id=item["id"],
                source_name=(f"Исходящее: {recipient} — {subject}" if is_outgoing else f"{sender} — {subject}"),
                source_url=f"https://mail.google.com/mail/u/0/#all/{item['id']}",
                source_sender=correspondent, source_thread_id=item.get("threadId"), content=content,
                attachments=attachments,
                routing_contract_id=routing_contract_id, routing_evidence=routing_evidence,
                routing_confidence=routing_confidence,
                automation_suppressed=bool(bulk_reason),
                automation_suppression_reason=bulk_reason,
            ), db, user, mailbox_origin=ingress_origin)
            processed += 1 if result["status"] else 0
            if not bulk_reason and result.get("context_confirmed"):
                discover_contact_from_message(
                    db, target_project_id, correspondent, content, user,
                    mail_connection_id=(mailbox_runtime.mail_connection_id if mailbox_write else None),
                    source_message_id=result.get("id"),
                )
            if not is_outgoing and not bulk_reason:
                notify_telegram(_gmail_telegram_notice(sender, subject, result))
        except Exception as exc:
            db.rollback()
            failed += 1
            errors.append({"item_index": processed + skipped + failed, "error": exc.__class__.__name__})
    db.add(AuditLog(action="gmail_sync", entity_type="project", entity_id=project_id,
                    details=f"processed={processed}; skipped={skipped}; failed={failed}"))
    db.commit()
    return {"processed": processed, "skipped": skipped, "failed": failed, "errors": errors[:20]}


@router.post("/ai-secretary/inbox/{message_id}/attachments/{attachment_index}/import")
def import_gmail_attachment(
    message_id: int,
    attachment_index: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    mode: Literal["CONFIRM"] = "CONFIRM",
):
    if mode != "CONFIRM":
        raise HTTPException(409, "Attachment import requires CONFIRM mode")
    source = db.get(Message, message_id)
    if source is None or source.source_type != "email":
        raise HTTPException(404, "Email message not found")
    require_project_role(db, user, source.project_id, "editor")
    try:
        mime_type, declared_size, attachment_id = attachment_declaration(
            source, attachment_index, max_bytes=MAX_ATTACHMENT_BYTES,
        )
        mailbox = runtime_for_message(db, source, actor=user, action=True)
    except (ValueError, GmailAttachmentDenied) as exc:
        raise HTTPException(409, "Mailbox origin is unavailable") from exc
    if mailbox is None:
        # Historical/project-token fallback is never sufficient for staging.
        raise HTTPException(409, "Mailbox origin is unavailable")
    try:
        mailbox_authority = require_mailbox_authority(
            db, runtime=mailbox, actor=user, permission="action",
        )
    except ValueError as exc:
        raise HTTPException(409, "Mailbox origin is unavailable") from exc
    project = db.get(Project, source.project_id)
    if project is None or project.organization_id != source.organization_id:
        raise HTTPException(409, "Attachment scope is unavailable")
    binding = GmailAttachmentBinding(
        organization_id=source.organization_id,
        owner_user_id=user.id,
        project_id=source.project_id,
        message_id=source.id,
        attachment_index=attachment_index,
        identity_id=mailbox.identity_id,
        mail_connection_id=mailbox.mail_connection_id,
        credential_generation=mailbox.generation,
        binding_epoch=mailbox.binding_epoch,
        mailbox_flags_record_version=mailbox.flags.record_version,
        mailbox_authority_version=mailbox_authority.authority_version,
        source_reference_id=mailbox.source_reference_id,
        source_version_id=mailbox.source_version_id,
        mailbox_binding_id=mailbox.binding_id,
        declared_mime_type=mime_type,
        declared_size=declared_size,
        mode=mode,
    )
    service = google_workspace_for_mailbox(mailbox.google_token_id, db).service("gmail", "v1")
    provider = GmailProviderDownloadAdapter(
        service,
        provider_message_id=mailbox.provider_message_id,
        provider_attachment_id=attachment_id,
        expected_size=declared_size,
        max_bytes=MAX_ATTACHMENT_BYTES,
    )
    try:
        staged = stage_gmail_attachment(db, binding, provider, max_bytes=MAX_ATTACHMENT_BYTES)
        db.add(AuditLog(
            action="gmail_attachment_staged",
            entity_type="message",
            entity_id=source.id,
            details=f"staging_id={staged.staging_id};status=admitted",
        ))
        job = enqueue_staged_gmail_attachment(db, staged.staging_id)
    except GmailAttachmentUnavailable as exc:
        db.rollback()
        raise HTTPException(503, "Attachment staging is unavailable") from exc
    except GmailAttachmentIntegrityError as exc:
        db.rollback()
        raise HTTPException(422, "Attachment integrity validation failed") from exc
    except GmailAttachmentDenied as exc:
        db.rollback()
        raise HTTPException(409, "Attachment import is not authorized") from exc
    return {
        "staging_id": staged.staging_id,
        "job_id": job.id,
        "status": job.status,
        "already_queued": staged.duplicate or job.attempts > 0 or job.status != "queued",
    }


@router.post("/response-drafts/{draft_id}/send-gmail")
def send_gmail(draft_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    draft = db.get(ResponseDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "Response draft not found")
    require_project_role(db, user, draft.project_id, "manager")
    if draft.source_file_name == "corrective-follow-up":
        raise HTTPException(409, "Corrective follow-up requires separate CONFIRM provider action")
    source = db.get(Message, draft.message_id) if draft.message_id else None
    try:
        mailbox = runtime_for_message(db, source, actor=user, action=True) if source is not None else None
    except ValueError as exc:
        raise HTTPException(409, "Mailbox origin is unavailable") from exc
    if draft.sent_external_id:
        return {"id": draft.id, "status": "sent", "gmail_message_id": draft.sent_external_id, "already_sent": True}
    if draft.status != "approved":
        raise HTTPException(409, "Сначала подтвердите и при необходимости отредактируйте проект ответа")
    recipient = draft.recipient_to or (
        parseaddr(source.source_sender)[1]
        if source is not None and source.source_type == "email" and source.source_sender else ""
    )
    if not recipient:
        raise HTTPException(422, "Не удалось определить адрес получателя")
    # The provider call is deliberately absent from the API lifecycle.  The
    # worker reloads this row and verifies the exact approved content hash,
    # mailbox generation and human authority immediately before the effect.
    try:
        queued = queue_confirmed_action(
            db, action_kind="gmail.message.send", target_id=draft.id, actor=user,
        )
    except ProviderActionError as exc:
        db.rollback()
        raise HTTPException(409, f"Gmail action is unavailable ({exc.code})") from exc
    draft = db.get(ResponseDraft, draft.id)
    if draft.status == "approved":
        draft.status = "sending"
        db.commit()
    return {"id": draft.id, "status": "queued", "gmail_message_id": None,
            "already_sent": False, **queued}
