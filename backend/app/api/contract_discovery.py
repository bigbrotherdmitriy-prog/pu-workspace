from __future__ import annotations

from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.organization_contract import Contract
from app.models.user import User


router = APIRouter(tags=["contracts"])


class ContractDiscoveryRequest(BaseModel):
    document_ids: list[int] = Field(min_length=1, max_length=200)


_NUMBER_RE = re.compile(
    r"(?:договор|контракт)\s*(?:(?:поставки|подряда|субподряда|оказания\s+услуг)\s*)?"
    r"(?:№|N|номер)?\s*[:№N-]?\s*"
    r"([A-ZА-ЯЁ0-9][A-ZА-ЯЁa-zа-яё0-9./_-]{2,80})",
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(
    r"\b((?:ООО|АО|ПАО|ЗАО|ИП|ФКУ|ФГУП|ГУП|МУП)\s*[«\"']?[^\n,;]{2,100}?[»\"']?)"
    r"(?=\s*(?:,|именуем|в лице|$))",
    re.IGNORECASE,
)


def _text_for_document(db: Session, document: Document) -> str:
    return db.scalar(select(DocumentVersion.content).where(
        DocumentVersion.document_id == document.id,
    ).order_by(DocumentVersion.version_number.desc()).limit(1)) or ""


def discover_contract_fields(name: str, content: str) -> dict:
    """Return a reviewable proposal. It never creates or links records."""
    text = " ".join((content or "").split())
    lowered = f"{name}\n{text[:20_000]}".casefold()
    number_match = _NUMBER_RE.search(text[:12_000]) or _NUMBER_RE.search(name)
    fallback_number = Path(name).stem.strip()[:255]
    number = (number_match.group(1).strip(" .,:;№") if number_match else fallback_number) or "Без номера"
    companies = []
    for value in _COMPANY_RE.findall(text[:15_000]):
        normalized = " ".join(value.split()).strip(" .,:;")
        if normalized.casefold() not in {item.casefold() for item in companies}:
            companies.append(normalized)

    if "государственн" in lowered and "контракт" in lowered or "генподряд" in lowered:
        kind, kind_reason = "prime_reference", "найден государственный/генподрядный контекст"
    elif "поставк" in lowered or "поставщик" in lowered:
        kind, kind_reason = "supply", "найдены признаки договора поставки"
    elif "субсубподряд" in lowered or "субподрядчик" in lowered or "субподряд" in lowered:
        kind, kind_reason = "downstream_subcontract", "найдены признаки субподряда"
    else:
        kind, kind_reason = "customer", "роль сторон требует проверки пользователя"

    legal_markers = sum(marker in lowered for marker in (
        "предмет договора", "права и обязанности", "цена договора", "срок действия",
        "реквизиты сторон", "заказчик", "подрядчик",
    ))
    confidence = min(0.95, 0.35 + (0.25 if number_match else 0) + legal_markers * 0.06)
    attachment_name = bool(re.search(
        r"(?:^|\W)(?:приложени|спецификац|график|ведомост|смет|техническ.*задани)",
        Path(name).stem.casefold(),
    ))
    is_contract = not attachment_name and (bool(number_match) or legal_markers >= 2)
    return {
        "number": number,
        "title": fallback_number,
        "counterparty": companies[-1] if companies else None,
        "contract_kind": kind,
        "confidence": round(confidence, 2),
        "is_contract": is_contract,
        "evidence": [kind_reason, *( ["номер найден в тексте"] if number_match else ["номер взят из имени файла"]),
                     f"юридических признаков: {legal_markers}",
                     *( ["файл похож на приложение, а не на самостоятельный договор"] if attachment_name else [])],
    }


@router.post("/projects/{project_id}/contracts/discover-bulk")
def discover_contracts_bulk(
    project_id: int,
    payload: ContractDiscoveryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "editor")
    documents = list(db.scalars(select(Document).where(
        Document.project_id == project_id,
        Document.id.in_(payload.document_ids),
    )))
    if len(documents) != len(set(payload.document_ids)):
        raise HTTPException(404, "Один или несколько документов проекта не найдены")
    linked_ids = set(db.scalars(select(Contract.source_document_id).where(
        Contract.project_id == project_id,
        Contract.source_document_id.is_not(None),
    )))
    proposals = []
    rejected = []
    content_by_document: dict[int, str] = {}
    for document in documents:
        if document.mime_type and "folder" in document.mime_type:
            continue
        content = _text_for_document(db, document)
        content_by_document[document.id] = content
        proposal = discover_contract_fields(document.name, content)
        if not proposal.pop("is_contract"):
            rejected.append({
                "document_id": document.id, "document_name": document.name,
                "reason": proposal["evidence"][-1] if proposal["evidence"] else "недостаточно признаков договора",
            })
            continue
        proposals.append({
            "document_id": document.id,
            "document_name": document.name,
            "already_linked": document.id in linked_ids,
            "parent_document_id": None,
            **proposal,
        })

    by_id = {item["document_id"]: item for item in proposals}
    roots = [item for item in proposals if item["contract_kind"] in {"prime_reference", "customer"}]
    for child in proposals:
        if child["contract_kind"] in {"prime_reference", "customer"}:
            continue
        body = re.sub(r"[^0-9a-zа-яё]+", "", content_by_document.get(child["document_id"], "").casefold())
        referenced = [item for item in proposals if item is not child and len(re.sub(r"\W+", "", item["number"])) >= 4
                      and re.sub(r"[^0-9a-zа-яё]+", "", item["number"].casefold()) in body]
        parent = referenced[0] if referenced else (roots[0] if len(roots) == 1 else None)
        if parent:
            child["parent_document_id"] = parent["document_id"]
            child["evidence"].append(f"вышестоящий договор: {by_id[parent['document_id']]['number']}")
    return {
        "proposals": proposals, "count": len(proposals), "rejected": rejected,
        "rejected_count": len(rejected), "originals_changed": False,
    }
