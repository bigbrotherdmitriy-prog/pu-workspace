"""Automatic invoice intake from Yandex Mail attachments (proposal-only)."""
from __future__ import annotations

import hashlib
import logging
from decimal import Decimal
from email.message import Message as EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.management import Notification
from app.models.project_member import ProjectMember


logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
INVOICE_EXTENSIONS = (".pdf", ".docx", ".doc", ".xlsx", ".xls")
INVOICE_MARKERS = ("счет", "сч-", "сч.", "invoice", "на оплату")
NOTIFY_ROLES = {"owner", "admin", "editor"}


def _decoded(value):
    from app.api.yandex_mail import _decoded as decoded
    return decoded(value)


def _normalize(value: str) -> str:
    return (value or "").casefold().replace("ё", "е")


def _looks_like_invoice(filename: str, subject: str) -> bool:
    haystack = _normalize(f"{filename} {subject}")
    return any(marker in haystack for marker in INVOICE_MARKERS)


def _format_amount(amount) -> str:
    try:
        value = Decimal(str(amount))
    except Exception:
        return "сумма не распознана"
    rounded = value.quantize(Decimal("1"))
    return f"{rounded:,}".replace(",", " ")


def _notification_body(counterparty: str | None, amount) -> str:
    who = counterparty or "Контрагент не распознан"
    if amount is None:
        return f"{who} — сумма не распознана. Нажмите, чтобы подтвердить."
    return f"{who} — {_format_amount(amount)} ₽. Нажмите, чтобы подтвердить."


def _recipients(db: Session, project_id: int, user_id: int) -> list[int]:
    rows = list(db.scalars(select(ProjectMember).where(
        ProjectMember.project_id == project_id,
    )))
    result: list[int] = []
    seen: set[int] = set()
    for row in rows:
        if row.role not in NOTIFY_ROLES or row.user_id in seen:
            continue
        seen.add(row.user_id)
        result.append(row.user_id)
    if user_id not in seen:
        result.append(user_id)
    return result


def _process_attachment(
    db: Session, *, project_id: int, message_external_id: str, subject: str,
    filename: str, mime_type: str, data: bytes, index: int, user_id: int,
) -> int | None:
    from app.api.execution_finance import (
        _apply_invoice_extraction_fields,
        _mark_own_outgoing_invoice,
        _audit,
        _ensure_default_categories,
        _project_organization_id,
    )
    from app.invoice_extraction import extract_invoice_fields
    from app.models.execution_finance import InvoiceExtractionProposal
    from app.organizer_engine.content import extract_text

    text = extract_text(data, mime_type, filename)
    if not text or not text.strip():
        return None

    external_id = f"{message_external_id}:att:{index}"
    existing = db.scalar(select(Document.id).where(
        Document.project_id == project_id,
        Document.source == "yandex_mail",
        Document.external_id == external_id,
    ))
    if existing is not None:
        return None

    digest = hashlib.sha256(text.encode()).hexdigest()
    document = Document(
        project_id=project_id, source="yandex_mail", external_id=external_id,
        name=filename, mime_type=mime_type, status="analyzed",
        content_hash=digest, summary=text[:700], current_version=1,
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        document_id=document.id, version_number=1, content=text,
    )
    db.add(version)
    db.flush()

    organization_id = _project_organization_id(db, project_id)
    categories = [row for row in _ensure_default_categories(db, organization_id) if row.is_active]
    fields = extract_invoice_fields(
        db, project_id, text, filename, [row.name for row in categories],
    )
    proposal = InvoiceExtractionProposal(
        project_id=project_id, source_document_id=document.id,
        source_document_version_id=version.id, source_document_sha256=digest,
        target_kind="cash_flow", status="proposed",
    )
    _apply_invoice_extraction_fields(proposal, fields, categories)
    _mark_own_outgoing_invoice(db, proposal, text)
    db.add(proposal)
    db.flush()
    _audit(
        db, "invoice_extraction_proposed_from_mail", "invoice_extraction_proposal",
        proposal.id, user_id,
        f"document={document.id}; version={version.id}; method={fields.extraction_method}",
    )

    if proposal.fallback_reason == "own_outgoing_invoice":
        return proposal.id  # our own outgoing invoice: no «pay this» push
    body = _notification_body(fields.counterparty, fields.amount)
    for uid in _recipients(db, project_id, user_id):
        db.add(Notification(
            project_id=project_id, user_id=uid, kind="invoice_from_mail",
            title="Счёт из почты", body=body,
            entity_type="invoice_extraction_proposal", entity_id=proposal.id,
            dedupe_key=f"invoice-from-mail:{proposal.id}:{uid}",
        ))
    return proposal.id


def intake_invoice_attachments(
    db: Session, *, project_id: int, message_external_id: str, subject: str,
    parsed: EmailMessage, user_id: int,
) -> list[int]:
    created: list[int] = []
    try:
        for index, part in enumerate(parsed.walk()):
            try:
                filename = _decoded(part.get_filename())
                if not filename or part.is_multipart():
                    continue
                name_lower = filename.casefold()
                if not name_lower.endswith(INVOICE_EXTENSIONS):
                    continue
                data = part.get_payload(decode=True) or b""
                if len(data) < 1 or len(data) > MAX_ATTACHMENT_BYTES:
                    continue
                if not _looks_like_invoice(filename, subject):
                    continue
                with db.begin_nested():
                    proposal_id = _process_attachment(
                        db, project_id=project_id,
                        message_external_id=message_external_id, subject=subject,
                        filename=filename, mime_type=part.get_content_type()[:200],
                        data=data, index=index, user_id=user_id,
                    )
                if proposal_id is not None:
                    created.append(proposal_id)
            except Exception:
                logger.warning("invoice intake failed for attachment")
                continue
    except Exception:
        logger.warning("invoice intake failed")
    return created
