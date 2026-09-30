from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
import imaplib
import json
import re
import ssl

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.ai_secretary import IncomingMessage, ingest_message
from app.api.gmail import _automated_sender_reason, _bulk_email_reason
from app.core.auth import require_project_role, require_user
from app.core.token_crypto import TokenEncryptionError, decrypt_token, encrypt_token
from app.database import get_db
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.integration_credential import IntegrationCredential
from app.models.project import Project
from app.models.user import User


router = APIRouter(prefix="/projects", tags=["yandex-mail"])
IMAP_HOST = "imap.yandex.ru"
IMAP_PORT = 993
MAX_MESSAGE_BYTES = 15 * 1024 * 1024


class YandexMailConnection(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    app_password: SecretStr = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+", normalized):
            raise ValueError("Введите полный адрес ящика")
        return normalized


class YandexMailSyncRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    max_results: int = Field(default=25, ge=1, le=100)


class _HTMLText(HTMLParser):
    suppressed = {"head", "style", "script", "noscript", "iframe", "object", "embed", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.casefold()
        if tag in self.suppressed:
            self.depth += 1
        elif self.depth == 0 and tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        tag = tag.casefold()
        if tag in self.suppressed:
            self.depth = max(0, self.depth - 1)
        elif self.depth == 0 and tag in {"p", "div", "li", "tr", "table", "section", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if self.depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts).replace("\xa0", " ")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def _decoded(value: str | None, fallback: str = "") -> str:
    if not value:
        return fallback
    try:
        return str(make_header(decode_header(value))).strip() or fallback
    except (LookupError, UnicodeError):
        return value.strip() or fallback


def _part_text(part: EmailMessage) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeError):
        raw = part.get_payload(decode=True) or b""
        content = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    return content if isinstance(content, str) else ""


def _message_text(message: EmailMessage) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(_part_text(part))
        elif content_type == "text/html":
            parser = _HTMLText()
            parser.feed(_part_text(part))
            parser.close()
            html.append(parser.text())
    return "\n\n".join(value.strip() for value in (plain or html) if value.strip())


def _attachments(message: EmailMessage) -> list[dict]:
    result: list[dict] = []
    for part in message.walk():
        filename = _decoded(part.get_filename())
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        result.append({
            "name": filename[:500],
            "mime_type": part.get_content_type()[:200],
            "size": len(payload),
            "provider": "yandex_mail",
        })
        if len(result) == 100:
            break
    return result


def _headers(message: EmailMessage) -> dict[str, str]:
    names = ("from", "to", "cc", "date", "message-id", "references", "in-reply-to",
             "list-unsubscribe", "list-id", "precedence", "auto-submitted", "x-auto-response-suppress")
    return {name: _decoded(message.get(name)) for name in names if message.get(name)}


def _credential(project_id: int, db: Session) -> IntegrationCredential | None:
    return db.scalar(select(IntegrationCredential).where(
        IntegrationCredential.project_id == project_id,
        IntegrationCredential.provider == "yandex_mail",
        IntegrationCredential.capability == "channel",
    ))


def _credentials(project_id: int, db: Session) -> tuple[str, str]:
    row = _credential(project_id, db)
    if not row or not row.access_token or not row.account_email:
        raise HTTPException(401, "Яндекс Почта не подключена")
    try:
        password = decrypt_token(row.access_token)
    except TokenEncryptionError as exc:
        raise HTTPException(503, "Не удалось расшифровать учётные данные Яндекс Почты") from exc
    if not password:
        raise HTTPException(401, "Пароль приложения Яндекс Почты отсутствует")
    return row.account_email, password


def _new_imap():
    return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=20)


def _uid_validity(client) -> str:
    _status, values = client.response("UIDVALIDITY")
    value = (values or [b"unknown"])[0]
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")
    return str(value or "unknown")


@contextmanager
def _mailbox(email: str, password: str):
    client = None
    try:
        client = _new_imap()
        client.login(email, password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error("INBOX is unavailable")
        yield client
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def _provider_error(exc: Exception, *, authentication: bool = False) -> HTTPException:
    if isinstance(exc, imaplib.IMAP4.error):
        return HTTPException(401 if authentication else 502,
                             "Яндекс отклонил авторизацию. Проверьте пароль приложения")
    return HTTPException(503, "Сервер Яндекс Почты недоступен")


@router.put("/{project_id}/yandex-mail")
def connect_yandex_mail(
    project_id: int, payload: YandexMailConnection,
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    password = payload.app_password.get_secret_value()
    try:
        with _mailbox(payload.email, password):
            pass
    except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
        raise _provider_error(exc, authentication=True) from exc
    try:
        encrypted = encrypt_token(password)
    except TokenEncryptionError as exc:
        raise HTTPException(503, "Шифрование credentials не настроено") from exc
    row = _credential(project_id, db) or IntegrationCredential(
        project_id=project_id, provider="yandex_mail", capability="channel")
    row.access_token = encrypted
    row.refresh_token = None
    row.token_uri = None
    row.expires_at = None
    row.scopes = "imap.readonly"
    row.account_external_id = payload.email
    row.account_email = payload.email
    db.add(row)
    db.add(AuditLog(action="mail_channel_connected", entity_type="project", entity_id=project_id,
                    details="provider=yandex_mail; capability=imap.readonly"))
    db.commit()
    return {"connected": True, "provider": "yandex_mail", "account_email": payload.email, "read_only": True}


@router.get("/{project_id}/yandex-mail/status")
def yandex_mail_status(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "viewer")
    row = _credential(project_id, db)
    return {"connected": bool(row and row.access_token), "provider": "yandex_mail",
            "account_email": row.account_email if row else None, "read_only": True}


@router.delete("/{project_id}/yandex-mail")
def disconnect_yandex_mail(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, project_id, "manager")
    row = _credential(project_id, db)
    if row:
        db.delete(row)
    db.add(AuditLog(action="mail_channel_disconnected", entity_type="project", entity_id=project_id,
                    details="provider=yandex_mail"))
    db.commit()
    return {"disconnected": True}


@router.post("/{project_id}/yandex-mail/sync")
def sync_yandex_mail(
    project_id: int, payload: YandexMailSyncRequest,
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "editor")
    email, password = _credentials(project_id, db)
    since = (datetime.now(timezone.utc) - timedelta(days=payload.days)).strftime("%d-%b-%Y")
    processed = skipped = failed = 0
    errors: list[dict] = []
    try:
        with _mailbox(email, password) as client:
            uid_validity = _uid_validity(client)
            status, data = client.uid("search", None, "SINCE", since)
            if status != "OK":
                raise imaplib.IMAP4.error("SEARCH failed")
            uids = (data[0] or b"").split()[-payload.max_results:]
            for raw_uid in reversed(uids):
                uid = raw_uid.decode("ascii", "strict")
                external_id = f"yandex:{project_id}:{uid_validity}:{uid}"
                if db.scalar(select(Message.id).where(
                    Message.mail_connection_id.is_(None), Message.source_type == "email",
                    Message.source_external_id == external_id,
                )):
                    skipped += 1
                    continue
                try:
                    fetch_status, rows = client.uid("fetch", uid, "(BODY.PEEK[] RFC822.SIZE)")
                    if fetch_status != "OK":
                        raise imaplib.IMAP4.error("FETCH failed")
                    raw = next((item[1] for item in rows if isinstance(item, tuple) and isinstance(item[1], bytes)), b"")
                    if not raw or len(raw) > MAX_MESSAGE_BYTES:
                        raise ValueError("message is empty or exceeds the size limit")
                    parsed = BytesParser(policy=policy.default).parsebytes(raw)
                    headers = _headers(parsed)
                    subject = _decoded(parsed.get("subject"), "(без темы)")
                    sender = _decoded(parsed.get("from"), "Неизвестный отправитель")
                    content = _message_text(parsed) or "Письмо не содержит текстового тела."
                    bulk_reason = _bulk_email_reason(headers, None, subject, content)
                    response_reason = _automated_sender_reason(headers)
                    result = ingest_message(IncomingMessage(
                        project_id=project_id, source_type="email", source_external_id=external_id,
                        source_name=subject, source_url="https://mail.yandex.ru/",
                        source_sender=sender, source_thread_id=headers.get("message-id") or None,
                        content=content[:100000], attachments=_attachments(parsed),
                        automation_suppressed=bool(bulk_reason), automation_suppression_reason=bulk_reason,
                        response_suppressed=bool(response_reason), response_suppression_reason=response_reason,
                    ), db, user)
                    stored = db.get(Message, result["id"])
                    if stored:
                        stored.mail_headers_json = json.dumps(headers, ensure_ascii=False)
                        stored.mail_labels_json = "[]"
                        db.commit()
                    processed += 1
                except Exception as exc:
                    db.rollback()
                    failed += 1
                    errors.append({"uid": uid, "reason": type(exc).__name__})
    except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
        raise _provider_error(exc) from exc
    db.add(AuditLog(action="mail_channel_sync", entity_type="project", entity_id=project_id,
                    details=f"provider=yandex_mail; processed={processed}; skipped={skipped}; failed={failed}"))
    db.commit()
    return {"provider": "yandex_mail", "processed": processed, "skipped": skipped,
            "failed": failed, "errors": errors[:20], "read_only": True}
